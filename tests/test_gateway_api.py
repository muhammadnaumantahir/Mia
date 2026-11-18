from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class StubProvider:
    provider_id = "stub-local"

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id="stub-model",
                capabilities=frozenset({"chat"}),
                context_window=8192,
                free_tier=True,
                healthy=True,
                enabled=True,
            )
        ]

    async def complete(self, candidate, request: ModelRequest):
        return f"Stub reply to: {request.prompt}"


def test_assistant_page_and_gateway_chat_api(tmp_path):
    registry = ProviderRegistry()
    registry.register(StubProvider())
    store = ObservatoryStore(tmp_path / "assistant.sqlite3")
    client = TestClient(create_app(store, ModelGateway(registry)))

    page = client.get("/assistant")
    assert page.status_code == 200
    assert "Message Mia" in page.text

    response = client.post(
        "/api/gateway/chat",
        json={"prompt": "Hello Mia", "task_type": "chat"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_id"] == "stub-local"
    assert payload["content"] == "Stub reply to: Hello Mia"

    events = store.list_events()
    assert events[0]["category"] == "ai_gateway"
    assert events[0]["outcome"] == "success"
