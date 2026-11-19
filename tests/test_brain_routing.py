import pytest

from mia.brain.capabilities import (
    CapabilityRegistry,
    normalise_category,
    profile_for,
    size_hint_billions,
)
from mia.brain.router import BrainRouter, NoSuitableModel, RoutingPreferences
from mia.gateway.contracts import (
    ModelCandidate,
    ModelRequest,
    ProviderResponse,
    RoutePolicy,
    TransientProviderError,
)
from mia.gateway.health import CandidateHealthTracker
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway


def cand(model, *, provider="p", caps=("chat",), ctx=16384, local=True, free=True,
         healthy=True, enabled=True, rel=0.5, lat=1000.0, in_cost=None, out_cost=None):
    return ModelCandidate(
        provider_id=provider, model_id=model, capabilities=frozenset(caps),
        context_window=ctx, free_tier=free, healthy=healthy, enabled=enabled,
        reliability_score=rel, latency_ms=lat, data_local=local,
        input_cost_per_million_usd=in_cost, output_cost_per_million_usd=out_cost,
    )


def req(task="chat", prompt="hello", **kw):
    return ModelRequest(task_type=task, prompt=prompt, estimated_input_tokens=50,
                        max_output_tokens=256, **kw)


def test_category_aliases_and_default():
    assert normalise_category("chat") == "conversation"
    assert normalise_category("CODE") == "coding"
    assert normalise_category("nonsense") == "conversation"
    assert normalise_category(None) == "conversation"
    assert "tools" in profile_for("tool").required_capabilities


def test_size_hint_is_only_a_hint():
    assert size_hint_billions("llama3.1:8b") == 8.0
    assert size_hint_billions("qwen2.5-coder:1.5b") == 1.5
    assert size_hint_billions("mistral") is None


def test_simple_task_prefers_light_model_and_strong_task_prefers_large():
    router = BrainRouter()
    small = cand("tiny:3b", lat=500, ctx=16384)
    big = cand("large:70b", lat=4000, ctx=65536)
    simple = router.route([big, small], req("simple"))
    assert simple.selected.model_id == "tiny:3b"
    strong = router.route([small, big], req("reasoning"))
    assert strong.selected.model_id == "large:70b"
    assert any("task category" in r for r in strong.reasons)


def test_missing_capability_is_rejected_with_reason():
    router = BrainRouter()
    decision = router.route(
        [cand("a", caps=("chat",)), cand("b", caps=("chat", "tools"))], req("tool")
    )
    assert decision.selected.model_id == "b"
    assert ("p/a", "lacks capability: tools") in decision.rejected


def test_runtime_refuted_capability_is_not_trusted():
    caps = CapabilityRegistry()
    liar = cand("liar", caps=("chat", "tools"))
    caps.record(liar, "tools", False, "never emitted a valid call")
    caps.record(liar, "tools", False, "never emitted a valid call")
    router = BrainRouter(caps)
    honest = cand("honest", caps=("chat", "tools"), lat=9000)
    decision = router.route([liar, honest], req("tool"))
    assert decision.selected.model_id == "honest"
    assert any("failed at runtime" in r for _, r in decision.rejected)
    # a later success clears the refutation
    caps.record(liar, "tools", True)
    assert "tools" not in caps.view(liar).refuted
    assert "tools" in caps.view(liar).verified


def test_no_suitable_model_raises_explanation_instead_of_picking_unsuitable():
    router = BrainRouter()
    tiny_ctx = cand("small-ctx", ctx=2048)
    with pytest.raises(NoSuitableModel) as exc:
        router.route([tiny_ctx], req("reasoning"))
    assert "context window" in str(exc.value)
    with pytest.raises(NoSuitableModel):
        router.route([], req("chat"))
    with pytest.raises(NoSuitableModel):
        router.route([cand("x", healthy=False), cand("y", enabled=False)], req())


def test_local_only_and_preferences():
    router = BrainRouter()
    remote = cand("remote", provider="r", local=False)
    local = cand("local", provider="l", local=True)
    d = router.route([remote, local], req(), preferences=RoutingPreferences(local_only=True))
    assert [c.model_id for c in d.ordered] == ["local"]
    d = router.route(
        [cand("a", provider="x"), cand("b", provider="y")],
        req(), preferences=RoutingPreferences(preferred_providers=("y",)),
    )
    assert d.selected.provider_id == "y"


def test_paid_models_stay_excluded_without_explicit_policy():
    router = BrainRouter()
    paid = cand("paid", free=False, in_cost=1.0, out_cost=1.0)
    free = cand("free")
    d = router.route([paid, free], req())
    assert [c.model_id for c in d.ordered] == ["free"]
    assert ("p/paid", "excluded by cost/quota policy") in d.rejected
    d = router.route([paid, free], req(), policy=RoutePolicy(allow_paid=True,
                                                             max_request_cost_usd=1.0))
    assert d.selected.model_id == "free"  # free still wins on cost


def test_rate_limited_candidates_are_skipped():
    health = CandidateHealthTracker()
    a, b = cand("a"), cand("b")
    health.record_failure(a, TransientProviderError("429", retry_after=60, rate_limited=True))
    d = BrainRouter(health=health).route([a, b], req())
    assert [c.model_id for c in d.ordered] == ["b"]


def test_decision_dict_is_diagnostic_safe():
    d = BrainRouter().route([cand("a"), cand("b")], req())
    info = d.to_dict()
    assert info["selected"].startswith("p/")
    assert "reasons" in info and "rejected" in info


class _P:
    def __init__(self, pid, fail=False):
        self.provider_id = pid
        self.fail = fail
        self.calls = 0

    async def health(self):
        return True

    async def list_models(self):
        return []

    async def complete(self, candidate, request):
        self.calls += 1
        if self.fail:
            raise TransientProviderError("down")
        return ProviderResponse(content=f"from {candidate.provider_id}")


async def test_gateway_order_hint_reorders_but_cannot_widen():
    registry = ProviderRegistry()
    a, b = _P("a"), _P("b")
    registry.register(a)
    registry.register(b)
    gw = ModelGateway(registry)
    ca, cb = cand("m", provider="a", rel=0.9), cand("m", provider="b", rel=0.1)
    plain = await gw.complete(req(), [ca, cb])
    assert plain.provider_id == "a"
    hinted = await gw.complete(req(), [ca, cb], order_hint=[cb, ca])
    assert hinted.provider_id == "b"
    only_b = await gw.complete(req(), [ca, cb], order_hint=[cb])
    assert only_b.attempted_candidates == ("b/m",)
    ineligible = cand("m", provider="a", enabled=False)
    with pytest.raises(RuntimeError):
        await gw.complete(req(), [ineligible], order_hint=[ca])


async def test_fallback_follows_router_order_and_preserves_prompt():
    registry = ProviderRegistry()
    a, b = _P("a", fail=True), _P("b")
    registry.register(a)
    registry.register(b)
    gw = ModelGateway(registry)
    ca, cb = cand("m", provider="a"), cand("m", provider="b")
    decision = BrainRouter().route([ca, cb], req(), preferences=RoutingPreferences(
        preferred_providers=("a",)))
    result = await gw.complete(req(), [ca, cb], order_hint=list(decision.ordered))
    assert result.provider_id == "b"
    assert result.attempted_candidates == ("a/m", "b/m")
