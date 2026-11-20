from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest
from mia.gateway.model_registry import ModelRegistry
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
                data_local=True,
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


def test_gateway_console_and_persistent_model_controls(tmp_path):
    providers = ProviderRegistry()
    providers.register(StubProvider())
    model_store = ModelRegistry(tmp_path / "models.sqlite3")
    model_gateway = ModelGateway(providers, model_registry=model_store)
    client = TestClient(
        create_app(
            ObservatoryStore(tmp_path / "gateway.sqlite3"),
            model_gateway,
        )
    )

    page = client.get("/gateway")
    assert page.status_code == 200
    assert "AI Gateway" in page.text
    assert "textContent" in page.text

    refreshed = client.post("/api/gateway/models/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["count"] == 1

    overview = client.get("/api/gateway/overview")
    assert overview.status_code == 200
    model = overview.json()["models"][0]
    assert model["key"] == "stub-local/stub-model"
    assert model["effective_capabilities"] == ["chat"]
    assert overview.json()["budgets"]["paid_routing_available"] is False

    updated = client.post(
        "/api/gateway/models/stub-local/stub-model/settings",
        json={"enabled": False, "preference": 6, "alias": "fast", "change_alias": True},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["preference"] == 6
    assert updated.json()["alias"] == "fast"

    persisted = ModelRegistry(tmp_path / "models.sqlite3").get("stub-local", "stub-model")
    assert persisted is not None
    assert persisted.enabled is False
    assert persisted.preference == 6
    assert persisted.alias == "fast"


def test_gateway_model_controls_reject_unknown_fields_and_invalid_values(tmp_path):
    providers = ProviderRegistry()
    providers.register(StubProvider())
    model_store = ModelRegistry(tmp_path / "models.sqlite3")
    model_gateway = ModelGateway(providers, model_registry=model_store)
    client = TestClient(
        create_app(
            ObservatoryStore(tmp_path / "gateway-invalid.sqlite3"),
            model_gateway,
        )
    )
    client.post("/api/gateway/models/refresh")

    invalid = client.post(
        "/api/gateway/models/stub-local/stub-model/settings",
        json={"preference": 50},
    )
    assert invalid.status_code == 422

    missing = client.post(
        "/api/gateway/models/stub-local/not-found/settings",
        json={"enabled": False},
    )
    assert missing.status_code == 422
