import json

from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest, StreamChunk, TokenUsage
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.identity import identity_system_prompt
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

DECISION = "We decided to use PostgreSQL for the billing service for transactional safety."
QUESTION = "Which database did we choose for the billing service?"


class RecordingProvider:
    """Captures exactly what the model would see."""

    def __init__(self, provider_id, *, data_local=True, model_id="model", reply="Noted."):
        self.provider_id = provider_id
        self.data_local = data_local
        self.model_id = model_id
        self.reply = reply
        self.requests: list[ModelRequest] = []

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id=self.model_id,
                capabilities=frozenset({"chat"}),
                context_window=8192,
                free_tier=True,
                healthy=True,
                enabled=True,
                data_local=self.data_local,
            )
        ]

    async def complete(self, candidate, request: ModelRequest):
        self.requests.append(request)
        return self.reply

    async def stream(self, candidate, request: ModelRequest):
        self.requests.append(request)
        yield StreamChunk(delta=self.reply)
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(5, 2))

    @property
    def last_messages(self):
        return list(self.requests[-1].messages)

    def memory_blocks(self):
        return [
            m for m in self.last_messages
            if m["role"] == "system" and "<memory_data>" in m["content"]
        ]


def make_app(tmp_path, monkeypatch, *providers, memory=None, observatory=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    observatory = observatory or ObservatoryStore(tmp_path / "observatory.sqlite3")
    memory = memory or MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(
        create_app(store=observatory, gateway=ModelGateway(registry), memory=memory)
    )
    return client, memory, observatory


def seed_decision(memory):
    return memory.add_item(
        kind="fact",
        title="Billing database decision",
        content=DECISION,
        source_title="Architecture review",
        trust_level="user",
    )


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        name, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def test_chat_retrieves_relevant_memory_with_provenance(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a")
    client, memory, _ = make_app(tmp_path, monkeypatch, provider)
    item_id = seed_decision(memory)

    response = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    assert response.status_code == 200
    refs = response.json()["context_references"]
    assert [r["ref_id"] for r in refs] == [item_id]
    assert refs[0]["provenance"] == "owner-provided" and refs[0]["source"] == "Architecture review"

    messages = provider.last_messages
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith(identity_system_prompt())
    assert "LIVE RUNTIME EVIDENCE" in messages[0]["content"]
    block = provider.memory_blocks()
    assert len(block) == 1 and "PostgreSQL" in block[0]["content"]
    assert messages[-1] == {"role": "user", "content": QUESTION}


def test_no_memory_block_when_nothing_relevant(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a")
    client, memory, _ = make_app(tmp_path, monkeypatch, provider)
    seed_decision(memory)
    response = client.post(
        "/api/gateway/chat", json={"prompt": "Tell me a joke about penguins", "use_tools": False}
    )
    assert response.status_code == 200
    assert response.json()["context_references"] == []
    assert provider.memory_blocks() == []
    assert len(provider.last_messages) == 2  # identity + the user turn


def test_continuity_survives_restart_and_model_switch(tmp_path, monkeypatch):
    first = RecordingProvider("local-a", model_id="small-model", reply="Understood, PostgreSQL.")
    client, memory, observatory = make_app(tmp_path, monkeypatch, first)
    seed_decision(memory)
    opening = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    conversation_id = opening.json()["conversation_id"]
    identity_before = first.last_messages[0]

    # "Restart": new store/app objects over the same files, different provider and model.
    second = RecordingProvider("local-b", model_id="other-model", reply="Still PostgreSQL.")
    client2, _, _ = make_app(
        tmp_path,
        monkeypatch,
        second,
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
        observatory=ObservatoryStore(tmp_path / "observatory.sqlite3"),
    )
    follow_up = client2.post(
        "/api/gateway/chat",
        json={
            "prompt": "Remind me why we chose that database for the billing service",
            "conversation_id": conversation_id,
            "use_tools": False,
        },
    )
    assert follow_up.status_code == 200
    identity_marker = "\\n\\nLIVE RUNTIME EVIDENCE"
    assert identity_before["content"].split(identity_marker, 1)[0] == second.last_messages[0]["content"].split(identity_marker, 1)[0]
    assert "Configured provider adapters (1): local-b" in second.last_messages[0]["content"]
    seen = json.dumps(second.last_messages)
    assert "Understood, PostgreSQL." in seen  # prior turns persisted across the restart
    assert len(second.memory_blocks()) == 1 and "transactional safety" in seen


def test_older_turns_beyond_the_recent_window_are_retrieved(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a")
    client, memory, _ = make_app(tmp_path, monkeypatch, provider)
    conversation = memory.ensure_conversation("long-conversation-1")
    memory.append_message(conversation, "user", "My project codename is Falcon-Nest and ships in March")
    for index in range(14):
        memory.append_message(
            conversation, "assistant" if index % 2 else "user", f"filler turn number {index}"
        )
    response = client.post(
        "/api/gateway/chat",
        json={
            "prompt": "When does the Falcon-Nest codename ship?",
            "conversation_id": conversation,
            "use_tools": False,
        },
    )
    assert response.status_code == 200
    refs = response.json()["context_references"]
    assert len(refs) == 1 and refs[0]["ref_type"] == "conversation"
    assert "ships in March" in provider.memory_blocks()[0]["content"]


def test_remote_provider_does_not_receive_memory_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", raising=False)
    remote = RecordingProvider("remote-a", data_local=False)
    client, memory, observatory = make_app(tmp_path, monkeypatch, remote)
    seed_decision(memory)

    response = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    assert response.status_code == 200
    assert response.json()["context_references"] == []
    assert remote.memory_blocks() == []
    assert "PostgreSQL" not in json.dumps(remote.last_messages)
    events = [e for e in observatory.list_events(50) if e["event_type"] == "context_withheld"]
    assert len(events) == 1 and events[0]["outcome"] == "withheld"


def test_remote_provider_receives_memory_only_after_owner_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", "true")
    remote = RecordingProvider("remote-a", data_local=False)
    client, memory, _ = make_app(tmp_path, monkeypatch, remote)
    seed_decision(memory)
    response = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    assert response.status_code == 200
    assert len(remote.memory_blocks()) == 1
    assert len(response.json()["context_references"]) == 1


def test_memory_is_routed_to_the_local_model_when_both_are_available(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", raising=False)
    remote = RecordingProvider("remote-a", data_local=False)
    local = RecordingProvider("local-a", data_local=True)
    client, memory, observatory = make_app(tmp_path, monkeypatch, remote, local)
    seed_decision(memory)

    response = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    assert response.status_code == 200
    assert remote.requests == []  # private memory never reached the remote provider
    assert len(local.memory_blocks()) == 1
    assembled = [e for e in observatory.list_events(50) if e["event_type"] == "context_assembled"]
    assert len(assembled) == 1
    payload = json.loads(assembled[0]["payload_json"])
    assert payload["local_only"] is True and payload["identity_version"]
    assert "PostgreSQL" not in json.dumps(payload)  # ids only, never content


def test_retrieval_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_CONTEXT_RETRIEVAL", "false")
    provider = RecordingProvider("local-a")
    client, memory, _ = make_app(tmp_path, monkeypatch, provider)
    seed_decision(memory)
    response = client.post("/api/gateway/chat", json={"prompt": QUESTION, "use_tools": False})
    assert response.status_code == 200
    assert response.json()["context_references"] == []
    assert provider.memory_blocks() == []


def test_stream_endpoint_uses_context_and_reports_references(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a", reply="PostgreSQL, per memory.")
    client, memory, _ = make_app(tmp_path, monkeypatch, provider)
    item_id = seed_decision(memory)

    response = client.post("/api/gateway/chat/stream", json={"prompt": QUESTION})
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[-1][0] == "done"
    assert [r["ref_id"] for r in events[-1][1]["context_references"]] == [item_id]
    assert provider.last_messages[0]["content"].startswith(identity_system_prompt())
    assert "LIVE RUNTIME EVIDENCE" in provider.last_messages[0]["content"]
    assert len(provider.memory_blocks()) == 1


def test_stream_withholds_memory_from_remote_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", raising=False)
    remote = RecordingProvider("remote-a", data_local=False)
    client, memory, _ = make_app(tmp_path, monkeypatch, remote)
    seed_decision(memory)
    response = client.post("/api/gateway/chat/stream", json={"prompt": QUESTION})
    events = parse_sse(response.text)
    assert events[-1][0] == "done" and events[-1][1]["context_references"] == []
    assert remote.memory_blocks() == []
