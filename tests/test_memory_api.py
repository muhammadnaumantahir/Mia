from fastapi.testclient import TestClient

from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_memory_item_api_preserves_provenance_and_allows_deletion(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            memory=memory,
        )
    )

    created = client.post(
        "/api/memory/items",
        json={
            "kind": "document",
            "title": "Mia research notes",
            "content": "The ten SPS layers remain unchanged.",
            "source_uri": "file:///research/notes.md",
            "source_title": "Research notes",
            "trust_level": "untrusted",
            "tags": ["SPS", "thesis"],
        },
    )
    assert created.status_code == 201
    item_id = created.json()["item_id"]

    search = client.get("/api/memory/search", params={"q": "SPS layers"})
    assert search.status_code == 200
    assert search.json()["items"][0]["item_id"] == item_id
    assert search.json()["items"][0]["source_uri"] == "file:///research/notes.md"
    assert search.json()["items"][0]["trust_level"] == "untrusted"

    deleted = client.delete(f"/api/memory/items/{item_id}")
    assert deleted.json()["deleted"] is True
    assert client.get("/api/memory/items").json()["items"] == []


class ConversationProvider:
    provider_id = "memory-test-provider"

    async def health(self):
        return True

    async def list_models(self):
        from mia.gateway.contracts import ModelCandidate

        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id="conversation-model",
                capabilities=frozenset({"chat"}),
                context_window=8192,
                free_tier=True,
                healthy=True,
                enabled=True,
            )
        ]

    async def complete(self, candidate, request):
        if request.prompt == "What project did I mention?":
            assert [message["role"] for message in request.messages] == [
                "user",
                "assistant",
                "user",
            ]
            assert request.messages[0]["content"] == "My project is Mia."
        return "I will keep the context in this conversation."


def test_chat_api_persists_and_reuses_conversation_id(tmp_path):
    from mia.gateway.registry import ProviderRegistry
    from mia.gateway.service import ModelGateway

    registry = ProviderRegistry()
    registry.register(ConversationProvider())
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            gateway=ModelGateway(registry),
            memory=memory,
        )
    )

    first = client.post(
        "/api/gateway/chat",
        json={"prompt": "My project is Mia."},
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]

    second = client.post(
        "/api/gateway/chat",
        json={
            "prompt": "What project did I mention?",
            "conversation_id": conversation_id,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id
    assert len(memory.get_messages(conversation_id)) == 4
    assert len(client.get("/api/conversations").json()["items"]) == 1



def test_memory_export_api_returns_checksummed_conversation_and_items(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    conversation_id = memory.ensure_conversation()
    memory.append_message(conversation_id, "user", "Export this conversation")
    memory.add_item(
        kind="note",
        title="Exportable note",
        content="This item remains user-owned memory.",
        trust_level="user",
    )
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            memory=memory,
        )
    )

    response = client.get("/api/memory/export")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert payload["conversations"][0]["conversation_id"] == conversation_id
    assert payload["memory_items"][0]["title"] == "Exportable note"
    assert len(payload["sha256"]) == 64
