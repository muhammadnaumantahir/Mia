import httpx
import pytest
import respx

from mia.gateway import factory
from mia.gateway.contracts import ModelRequest
from mia.gateway.providers import OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.gateway.urlpolicy import parse_timeout_seconds, validate_extra_headers


class FakeStore:
    keys: dict[str, str] = {}

    def get(self, provider_id):
        return self.keys.get(provider_id)


@pytest.fixture
def env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("MIA_"):
            monkeypatch.delenv(name)
    FakeStore.keys = {}
    monkeypatch.setattr(factory, "CredentialStore", FakeStore)
    return monkeypatch


def test_local_only_default_registers_only_ollama(env):
    assert factory.build_default_registry().ids() == ("ollama",)


@pytest.mark.parametrize("legacy_id", ["FREELLMAPI", "OMNIROUTE"])
def test_legacy_gateway_flags_do_not_register_sidecars(env, legacy_id):
    env.setenv(f"MIA_{legacy_id}_ENABLED", "true")
    env.setenv(f"MIA_{legacy_id}_BASE_URL", "https://legacy.example.com/v1")
    FakeStore.keys[legacy_id.lower()] = "sk-test"
    assert factory.build_default_registry().ids() == ("ollama",)


def test_generic_provider_flags_headers_and_timeout_are_applied(env):
    env.setenv("MIA_EXTRA_PROVIDERS", "cloud-one")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_BASE_URL", "https://cloud.example.com/v1")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_ROUTE_FREE", "true")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_TIMEOUT_SECONDS", "30")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_HEADERS", '{"X-Org": "mia"}')
    FakeStore.keys["cloud-one"] = "sk-test"

    registry = factory.build_default_registry()
    provider = registry.get("cloud-one")
    assert registry.ids() == ("cloud-one", "ollama")
    assert provider.free_tier is True
    assert provider.timeout == 30.0
    assert provider._headers()["X-Org"] == "mia"
    assert provider._headers()["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"Authorization": "x"}'])
def test_bad_header_config_fails_closed(env, raw):
    env.setenv("MIA_EXTRA_PROVIDERS", "cloud-one")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_BASE_URL", "https://cloud.example.com/v1")
    env.setenv("MIA_PROVIDER_CLOUD_ONE_HEADERS", raw)
    FakeStore.keys["cloud-one"] = "sk-test"
    with pytest.raises((RuntimeError, ValueError)):
        factory.build_default_registry()


def test_extra_provider_ids_validated_and_registered(env):
    env.setenv("MIA_EXTRA_PROVIDERS", "my-llm")
    env.setenv("MIA_PROVIDER_MY_LLM_BASE_URL", "https://llm.example.com/v1")
    FakeStore.keys["my-llm"] = "k"
    assert factory.build_default_registry().ids() == ("my-llm", "ollama")

    for reserved in ("ollama", "freellmapi", "omniroute"):
        env.setenv("MIA_EXTRA_PROVIDERS", reserved)
        with pytest.raises(RuntimeError, match="reserved"):
            factory.build_default_registry()

    env.setenv("MIA_EXTRA_PROVIDERS", "../x")
    with pytest.raises(RuntimeError, match="Invalid"):
        factory.build_default_registry()


@pytest.mark.parametrize("raw", [None, "", "nan", "inf", "0", "-5", "601", "abc"])
def test_invalid_timeouts_use_default(raw):
    assert parse_timeout_seconds(raw, 90.0) == 90.0


def test_header_validation_rejects_injection():
    for bad in (
        {"A\r\nB": "x"},
        {"X": "a\r\nInjected: 1"},
        {"Host": "evil"},
        {"Content-Type": "x"},
    ):
        with pytest.raises(ValueError):
            validate_extra_headers(bad)


@pytest.mark.asyncio
@respx.mock
async def test_connection_test_classifies_outcomes_without_leaking():
    registry = ProviderRegistry()
    registry.register(OpenAICompatibleProvider("good", "https://good.example.com/v1", api_key="sk-a"))
    registry.register(OpenAICompatibleProvider("badkey", "https://bad.example.com/v1", api_key="sk-secret"))
    registry.register(OpenAICompatibleProvider("down", "https://down.example.com/v1"))
    respx.get("https://good.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})
    )
    respx.get("https://bad.example.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": "sk-secret invalid"})
    )
    respx.get("https://down.example.com/v1/models").mock(side_effect=httpx.ConnectError("boom"))
    gateway = ModelGateway(registry)

    good = await gateway.test_connection("good")
    assert (good.ok, good.status, good.model_count) == (True, "ok", 2)
    assert good.latency_ms is not None
    bad = await gateway.test_connection("badkey")
    assert (bad.ok, bad.status) == (False, "auth_failed")
    assert "sk-secret" not in repr(bad)
    down = await gateway.test_connection("down")
    assert (down.ok, down.status) == (False, "unreachable")
    assert (await gateway.test_connection("nope")).status == "unknown_provider"


@pytest.mark.asyncio
@respx.mock
async def test_discovery_skips_provider_with_rejected_credentials():
    registry = ProviderRegistry()
    registry.register(OpenAICompatibleProvider("good", "https://good.example.com/v1", free_tier=True))
    registry.register(OpenAICompatibleProvider("badkey", "https://bad.example.com/v1"))
    respx.get("https://good.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}]})
    )
    respx.get("https://bad.example.com/v1/models").mock(return_value=httpx.Response(403))
    models = await ModelGateway(registry).discover_models()
    assert [m.provider_id for m in models] == ["good"]
    request = ModelRequest(task_type="chat", prompt="x")
    assert request.task_type == "chat"
