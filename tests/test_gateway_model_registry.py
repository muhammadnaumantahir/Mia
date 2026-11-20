import httpx
import pytest
import respx

from mia.gateway.contracts import ModelCandidate
from mia.gateway.model_registry import ModelRegistry, ModelRegistryError
from mia.gateway.providers import OpenAICompatibleProvider
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


def cand(provider="p", model="m", caps=("chat",), ctx=8192, local=False, free=False):
    return ModelCandidate(provider, model, frozenset(caps), ctx, free, True, True, data_local=local)


@pytest.fixture
def reg(tmp_path):
    return ModelRegistry(tmp_path / "models.sqlite3")


def test_sync_records_reported_capabilities_and_persists(tmp_path):
    reg = ModelRegistry(tmp_path / "m.sqlite3")
    reg.sync_discovered([cand(caps=("chat", "tools"))], ["p"])
    again = ModelRegistry(tmp_path / "m.sqlite3").get("p", "m")
    assert again is not None
    assert again.effective_capabilities() == {"chat", "tools"}
    assert again.verification() == "reported"
    assert again.capabilities["tools"] == {"reported": "supported"}


def test_precedence_tested_over_configured_over_reported(reg):
    reg.sync_discovered([cand(caps=("chat", "tools", "vision"))], ["p"])
    reg.set_capability("p", "m", "tools", False, "tested")  # provider claims it, test disproves
    reg.set_capability("p", "m", "vision", False, "configured")
    reg.set_capability("p", "m", "embeddings", True, "configured")
    rec = reg.get("p", "m")
    assert rec.effective_capabilities() == {"chat", "embeddings"}
    assert rec.verification() == "tested"
    # A later sync must not erase tested or configured evidence.
    reg.sync_discovered([cand(caps=("chat", "tools", "vision"))], ["p"])
    assert reg.get("p", "m").effective_capabilities() == {"chat", "embeddings"}
    reg.set_capability("p", "m", "tools", True, "tested")
    assert "tools" in reg.get("p", "m").effective_capabilities()


def test_unknown_capability_is_never_advertised(reg):
    reg.sync_discovered([cand(caps=("chat",))], ["p"])
    assert "tools" not in reg.get("p", "m").effective_capabilities()
    assert reg.get("p", "m").capabilities.get("tools") is None


def test_missing_model_becomes_stale_but_keeps_owner_settings(reg):
    reg.sync_discovered([cand(model="a"), cand(model="b")], ["p"])
    reg.set_alias("p", "b", "fast")
    reg.set_preference("p", "b", 5)
    reg.set_enabled("p", "b", False)
    reg.sync_discovered([cand(model="a")], ["p"])
    assert [r.model_id for r in reg.list()] == ["a"]
    stale = reg.get("p", "b")
    assert stale.stale and stale.alias == "fast" and stale.preference == 5 and not stale.enabled
    reg.sync_discovered([cand(model="a"), cand(model="b")], ["p"])  # returns
    back = reg.get("p", "b")
    assert not back.stale and back.alias == "fast" and back.preference == 5 and not back.enabled


def test_offline_provider_does_not_stale_its_models(reg):
    reg.sync_discovered([cand(provider="x", model="a")], ["x"])
    reg.sync_discovered([], [])  # x did not answer
    assert not reg.get("x", "a").stale
    reg.sync_discovered([], ["x"])  # x answered with an empty list
    assert reg.get("x", "a").stale


def test_alias_rules_and_resolution(reg):
    reg.sync_discovered([cand(model="a"), cand(model="b")], ["p"])
    reg.set_alias("p", "a", "Quick")
    assert reg.resolve("quick").model_id == "a"
    assert reg.resolve("p/b").model_id == "b"
    assert reg.resolve("nope") is None
    with pytest.raises(ModelRegistryError):
        reg.set_alias("p", "b", "quick")  # duplicate
    for bad in ("", "a b", "x/y", "-lead", "A" * 65):
        with pytest.raises(ModelRegistryError):
            reg.set_alias("p", "b", bad)
    reg.set_alias("p", "a", None)
    assert reg.resolve("quick") is None
    with pytest.raises(ModelRegistryError):
        reg.set_alias("p", "ghost", "g")


def test_preference_validation(reg):
    reg.sync_discovered([cand()], ["p"])
    for bad in (11, -11, True, 1.5):
        with pytest.raises(ModelRegistryError):
            reg.set_preference("p", "m", bad)
    reg.set_preference("p", "m", -3)
    assert reg.get("p", "m").preference == -3


def test_manual_registration_and_removal_rules(reg):
    reg.register_manual("P", "custom", capabilities=["chat", "tools"], context_window=32000,
                        local=True)
    rec = reg.get("p", "custom")
    assert rec.manual and rec.local and rec.verification() == "configured"
    reg.sync_discovered([], ["p"])  # manual models are never staled by discovery
    assert not reg.get("p", "custom").stale
    reg.remove("p", "custom")
    assert reg.get("p", "custom") is None
    reg.sync_discovered([cand(model="live")], ["p"])
    with pytest.raises(ModelRegistryError):
        reg.remove("p", "live")  # live discovered model
    with pytest.raises(ModelRegistryError):
        reg.register_manual("p", "x", capabilities=["Bad Cap"], context_window=10)
    with pytest.raises(ModelRegistryError):
        reg.register_manual("p", "x", capabilities=[], context_window=0)


def test_apply_filters_disabled_and_uses_effective_capabilities(reg):
    found = [cand(model="a", caps=("chat", "tools")), cand(model="b"), cand(model="new")]
    reg.sync_discovered(found[:2], ["p"])
    reg.set_enabled("p", "b", False)
    reg.set_capability("p", "a", "tools", False, "tested")
    result = reg.apply(found, registered_providers=["p"])
    by_id = {c.model_id: c for c in result}
    assert set(by_id) == {"a", "new"}  # b disabled; unseen model passes through untouched
    assert by_id["a"].capabilities == {"chat"}


def test_apply_adds_manual_models_only_for_registered_providers(reg):
    reg.register_manual("p", "hidden", capabilities=["chat"], context_window=4096, free_tier=True)
    reg.register_manual("gone", "other", capabilities=["chat"], context_window=4096)
    result = reg.apply([], registered_providers=["p"])
    assert [(c.provider_id, c.model_id, c.free_tier) for c in result] == [("p", "hidden", True)]
    reg.set_enabled("p", "hidden", False)
    assert reg.apply([], registered_providers=["p"]) == []


@pytest.mark.asyncio
@respx.mock
async def test_gateway_discovery_syncs_registry_and_respects_owner_settings(tmp_path):
    providers = ProviderRegistry()
    providers.register(OpenAICompatibleProvider("up", "https://up.example.com/v1"))
    providers.register(OpenAICompatibleProvider("down", "https://down.example.com/v1"))
    route = respx.get("https://up.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})
    )
    respx.get("https://down.example.com/v1/models").mock(side_effect=httpx.ConnectError("x"))
    registry = ModelRegistry(tmp_path / "m.sqlite3")
    registry.register_manual("down", "pinned-manual", capabilities=["chat"], context_window=4096)
    gateway = ModelGateway(providers, model_registry=registry)

    first = await gateway.discover_models()
    assert {(c.provider_id, c.model_id) for c in first} == {
        ("up", "m1"), ("up", "m2"), ("down", "pinned-manual")}
    registry.set_enabled("up", "m2", False)
    route.mock(return_value=httpx.Response(200, json={"data": [{"id": "m2"}]}))
    second = await gateway.discover_models()
    assert {(c.provider_id, c.model_id) for c in second} == {("down", "pinned-manual")}
    assert registry.get("up", "m1").stale  # dropped by the provider, kept in the registry


def test_persistent_model_preferences_affect_gateway_routing(reg):
    from mia.gateway.contracts import RoutePolicy, ModelRequest

    candidates = [
        cand(model="low"),
        cand(model="preferred"),
    ]
    reg.sync_discovered(candidates, ["p"])
    reg.set_preference("p", "preferred", 8)

    gateway = ModelGateway(ProviderRegistry(), model_registry=reg)
    request = ModelRequest(task_type="chat", prompt="hello")
    ordered = gateway._rank(candidates, request, RoutePolicy(), None)

    assert [item.model_id for item in ordered] == ["preferred", "low"]
