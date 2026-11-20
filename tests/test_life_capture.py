import pytest

from mia.life.capture import MAX_OPEN_CONVERSATION_ITEMS, capture_from_message, extract_curiosity
from mia.life.store import LifeStore


@pytest.mark.parametrize("text, topic", [
    ("I wonder how SQLite FTS5 ranks results", "how SQLite FTS5 ranks results"),
    ("I'm curious about local-first sync designs.", "local-first sync designs"),
    ("Hello! Please look into the Windows service wrapper options later.",
     "the Windows service wrapper options"),
    ("When you have time, research rate limiting patterns for me", "rate limiting patterns"),
    ("I'd love to know more about vector index compaction", "vector index compaction"),
])
def test_explicit_curiosity_is_extracted(text, topic):
    found = extract_curiosity(text)
    assert found is not None and found.title == f"Look into: {topic}"
    assert found.reason.startswith('You said in conversation: "')


@pytest.mark.parametrize("text", [
    "What is the capital of France?",
    "Can you research this now and tell me",          # immediate request, answered in chat
    "I wonder",                                        # no topic
    "I wonder about it",                               # too short
    "I wonder cryptocurrencies",                       # a single word is not a topic
    "I wonder how https://evil.example/x works",       # URLs are added deliberately
    "I'm curious about my api key rotation policy",    # secrets are never queued
    "Fix the bug in parser.py",
])
def test_ordinary_or_unsafe_messages_are_ignored(text):
    assert extract_curiosity(text) is None


def test_capture_queues_one_owner_visible_item_with_source(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    item_id = capture_from_message(life, "I wonder how bm25 normalisation works", "conversation-one")
    [item] = life.list_items()
    assert item["item_id"] == item_id and item["origin"] == "conversation"
    assert item["source_ref"] == {"ref": "conversation-one", "type": "conversation"}
    assert capture_from_message(life, "I wonder how bm25 normalisation works", "conversation-two") is None
    assert len(life.list_items()) == 1  # duplicate topic is not queued twice


def test_disabled_setting_and_emergency_stop_block_capture(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    life.update_settings({"conversation_capture_enabled": False})
    assert capture_from_message(life, "I wonder how bm25 normalisation works", "conversation-one") is None
    life.update_settings({"conversation_capture_enabled": True, "emergency_stop": True})
    assert capture_from_message(life, "I wonder how bm25 normalisation works", "conversation-one") is None
    assert life.list_items() == []


def test_open_conversation_items_are_capped(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    for i in range(MAX_OPEN_CONVERSATION_ITEMS):
        life.add_item(title=f"Look into: topic {i}", why_it_matters="x", origin="conversation")
    assert capture_from_message(life, "I wonder how bm25 normalisation works", "conversation-one") is None


def test_only_the_first_sentence_match_is_captured(tmp_path):
    life = LifeStore(tmp_path / "l.sqlite3")
    capture_from_message(
        life, "I wonder how alpha beta works. I wonder how gamma delta works.", "conversation-one")
    assert len(life.list_items()) == 1


# ---------------------------------------------------------------- end to end
from fastapi.testclient import TestClient  # noqa: E402

from mia.gateway.registry import ProviderRegistry  # noqa: E402
from mia.gateway.service import ModelGateway  # noqa: E402
from mia.memory.store import MemoryStore  # noqa: E402
from mia.observatory.api import create_app  # noqa: E402
from mia.observatory.store import ObservatoryStore  # noqa: E402
from tests.test_chat_memory_context import RecordingProvider  # noqa: E402


def chat_client(tmp_path, monkeypatch, provider=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_LIFE_ENABLED", "false")
    registry = ProviderRegistry()
    if provider:
        registry.register(provider)
    life = LifeStore(tmp_path / "l.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"), gateway=ModelGateway(registry),
        memory=MemoryStore(tmp_path / "m.sqlite3"), life=life,
    )
    return TestClient(app), life


def test_chat_message_queues_curiosity_and_ordinary_chat_does_not(tmp_path, monkeypatch):
    client, life = chat_client(tmp_path, monkeypatch, RecordingProvider("local-a"))
    ok = client.post("/api/gateway/chat", json={"prompt": "What is two plus two?", "use_tools": False})
    assert ok.status_code == 200 and life.list_items() == []
    reply = client.post("/api/gateway/chat",
                        json={"prompt": "I wonder how bm25 normalisation works", "use_tools": False})
    assert reply.status_code == 200
    [item] = life.list_items()
    assert item["origin"] == "conversation" and item["source_ref"]["ref"] == reply.json()["conversation_id"]


def test_streaming_chat_also_queues_curiosity(tmp_path, monkeypatch):
    client, life = chat_client(tmp_path, monkeypatch, RecordingProvider("local-a"))
    response = client.post("/api/gateway/chat/stream",
                           json={"prompt": "I'm curious about local-first sync designs", "use_tools": False})
    assert response.status_code == 200
    assert [i["title"] for i in life.list_items()] == ["Look into: local-first sync designs"]


def test_failed_turn_does_not_queue_and_capture_faults_do_not_break_chat(tmp_path, monkeypatch):
    client, life = chat_client(tmp_path, monkeypatch)  # no provider registered: the turn fails
    response = client.post("/api/gateway/chat",
                           json={"prompt": "I wonder how bm25 normalisation works", "use_tools": False})
    assert response.status_code >= 400 and life.list_items() == []


def test_capture_fault_never_breaks_chat(tmp_path, monkeypatch):
    import mia.observatory.api as api_module

    def boom(*args, **kwargs):
        raise RuntimeError("capture broke")

    monkeypatch.setattr(api_module, "capture_curiosity_from_message", boom)
    client, _ = chat_client(tmp_path, monkeypatch, RecordingProvider("local-a"))
    response = client.post("/api/gateway/chat",
                           json={"prompt": "I wonder how bm25 normalisation works", "use_tools": False})
    assert response.status_code == 200
