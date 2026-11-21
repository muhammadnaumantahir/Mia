"""Bounded quality escalation (MIA-GW-01). Deterministic; fake providers only.

These tests prove the policy and wiring behave as designed. They say nothing about
the real-world quality of any live model.
"""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest, ProviderResponse, RoutePolicy
from mia.gateway.escalation import (
    EscalationPolicy,
    decide_escalation,
    escalation_policy_from_env,
)
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

ON = EscalationPolicy(enabled=True)


def cand(model, *, local=True, free=True, reliability=0.5, latency=1000.0, ctx=16384,
         caps=("chat",), price=(None, None), healthy=True, enabled=True, provider="p"):
    return ModelCandidate(provider, model, frozenset(caps), ctx, free, healthy, enabled,
                          reliability_score=reliability, latency_ms=latency, data_local=local,
                          input_cost_per_million_usd=price[0], output_cost_per_million_usd=price[1])


REQ = ModelRequest(task_type="conversation", prompt="hello there", estimated_input_tokens=100,
                   max_output_tokens=500, required_capabilities=frozenset({"chat"}))
SMALL, BIG = cand("tiny-1b"), cand("big-8b")


def decide(policy=ON, *, confidence="low", codes=("empty_response",), candidates=(SMALL, BIG),
           route=RoutePolicy(), used=SMALL, request=REQ, **kw):
    return decide_escalation(policy=policy, confidence=confidence, issue_codes=codes, request=request,
                             candidates=candidates, route_policy=route, used=used, **kw)


def test_disabled_by_default():
    assert EscalationPolicy().enabled is False
    d = decide(EscalationPolicy())
    assert not d.escalate and d.reason == "escalation disabled"


def test_escalates_to_stronger_free_local_model_with_bounded_request():
    d = decide(policy=EscalationPolicy(enabled=True, max_extra_output_tokens=200))
    assert d.escalate and d.target.model_id == "big-8b"
    assert d.request.max_output_tokens == 200 and d.request.tools == ()
    assert ("p/tiny-1b", "already tried for this turn") in d.rejected


@pytest.mark.parametrize("confidence,codes", [
    ("high", ()), ("medium", ("low_confidence",)), ("low", ("unverified_completion_claim",)),
])
def test_only_low_confidence_with_an_eligible_issue_escalates(confidence, codes):
    assert not decide(confidence=confidence, codes=codes).escalate


def test_never_escalates_after_side_effects():
    d = decide(had_side_effects=True)
    assert not d.escalate and "repeat an action" in d.reason


def test_attempt_limit_and_hard_ceiling():
    assert not decide(attempts_so_far=1).escalate
    p = EscalationPolicy(enabled=True, max_extra_attempts=99)
    assert decide(p, attempts_so_far=1).escalate and not decide(p, attempts_so_far=2).escalate


def test_private_requests_never_leave_local_models():
    cloud = cand("cloud-70b", local=False)
    d = decide(candidates=(SMALL, cloud), request=replace(REQ, sensitive=True))
    assert not d.escalate
    assert ("p/cloud-70b", "private request may only use a local model") in d.rejected


def test_target_must_differ_and_be_demonstrably_stronger():
    same_size = cand("other-1b")
    assert not decide(candidates=(SMALL, same_size)).escalate
    # unknown sizes: needs a clearly higher reliability score
    a, b = cand("alpha", reliability=0.5), cand("beta", reliability=0.55)
    assert not decide(candidates=(a, b), used=a).escalate
    c = cand("gamma", reliability=0.7)
    assert decide(candidates=(a, c), used=a).target.model_id == "gamma"
    assert not decide(candidates=(SMALL, BIG), tried=["p/big-8b"]).escalate


def test_latency_context_capability_and_health_limits():
    for bad in (cand("big-8b", latency=60_000.0), cand("big-8b", ctx=100),
                cand("big-8b", caps=("embedding",)), cand("big-8b", healthy=False),
                cand("big-8b", enabled=False)):
        assert not decide(candidates=(SMALL, bad)).escalate


def test_paid_escalation_fails_closed_without_both_permissions_known_price_and_budget():
    paid = cand("big-8b", free=False, price=(1.0, 2.0))
    allow = EscalationPolicy(enabled=True, allow_paid=True, max_extra_cost_usd=1.0)
    route = RoutePolicy(allow_paid=True, max_request_cost_usd=1.0)
    assert decide(allow, candidates=(SMALL, paid), route=route).target.model_id == "big-8b"
    assert not decide(ON, candidates=(SMALL, paid), route=route).escalate          # escalation not opted in
    assert not decide(allow, candidates=(SMALL, paid)).escalate                    # route policy blocks paid
    unknown = cand("big-8b", free=False, price=(None, None))
    d = decide(allow, candidates=(SMALL, unknown), route=route)
    assert not d.escalate and ("p/big-8b", "price unknown") in d.rejected
    tight = replace(allow, max_extra_cost_usd=0.0000001)
    assert not decide(tight, candidates=(SMALL, paid), route=route).escalate       # over cost cap


def test_free_target_preferred_over_paid_even_if_paid_is_stronger():
    paid = cand("huge-70b", free=False, price=(0.1, 0.1), reliability=0.99)
    allow = EscalationPolicy(enabled=True, allow_paid=True, max_extra_cost_usd=1.0)
    d = decide(allow, candidates=(SMALL, BIG, paid), route=RoutePolicy(allow_paid=True, max_request_cost_usd=1.0))
    assert d.target.model_id == "big-8b"


def test_env_policy_is_opt_in_and_invalid_values_fail_closed(monkeypatch):
    for name in ("MIA_ESCALATION_ENABLED", "MIA_ESCALATION_ALLOW_PAID", "MIA_ESCALATION_MAX_COST_USD"):
        monkeypatch.delenv(name, raising=False)
    p = escalation_policy_from_env()
    assert not p.enabled and not p.allow_paid and p.max_extra_cost_usd == 0.0
    monkeypatch.setenv("MIA_ESCALATION_ENABLED", "true")
    monkeypatch.setenv("MIA_ESCALATION_MAX_COST_USD", "nan")
    monkeypatch.setenv("MIA_ESCALATION_MAX_LATENCY_MS", "-5")
    p = escalation_policy_from_env()
    assert p.enabled and not p.allow_paid and p.max_extra_cost_usd == 0.0 and p.max_extra_latency_ms == 30_000.0


# ---------------------------------------------------------------- end to end (fake providers)

class Fake:
    def __init__(self, pid, model, reply, *, local=True):
        self.provider_id, self.model, self.reply, self.local, self.calls = pid, model, reply, local, 0

    async def health(self):
        return True

    async def list_models(self):
        return [ModelCandidate(self.provider_id, self.model, frozenset({"chat"}), 16384, True, True, True,
                               data_local=self.local)]

    async def complete(self, candidate, request):
        self.calls += 1
        return ProviderResponse(content=self.reply)


def client_for(tmp_path, monkeypatch, *providers):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    registry = ProviderRegistry()
    for p in providers:
        registry.register(p)
    app = create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3"), gateway=ModelGateway(registry),
                     memory=MemoryStore(tmp_path / "memory.sqlite3"))
    return TestClient(app)


WEAK = "I think it probably works, I have checked it, not sure though."
GOOD = "DNS maps names to addresses."


def chat(client, conv):
    r = client.post("/api/gateway/chat", json={"prompt": "Explain how DNS works please",
                                               "use_tools": False, "conversation_id": conv})
    assert r.status_code == 200, r.text
    return r.json()


def turn_events(client, name):
    return [e for t in client.get("/api/brain/diagnostics").json()["turns"]
            for e in t.get("events", []) if e.get("event") == name]


def test_pipeline_does_not_escalate_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_ESCALATION_ENABLED", raising=False)
    weak, strong = Fake("a", "tiny-1b", WEAK), Fake("b", "big-70b", GOOD)
    result = chat(client_for(tmp_path, monkeypatch, weak, strong), "conv-esc-off-1")
    assert (weak.calls, strong.calls) == (1, 0)
    assert result["content"] == WEAK and result["verification"]["confidence"] == "low"


def test_pipeline_escalates_once_to_a_stronger_model_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_ESCALATION_ENABLED", "true")
    weak, strong = Fake("a", "tiny-1b", WEAK), Fake("b", "big-70b", GOOD)
    client = client_for(tmp_path, monkeypatch, weak, strong)
    result = chat(client, "conv-esc-on-1")
    assert (weak.calls, strong.calls) == (1, 1)
    assert result["content"] == GOOD and result["provider_id"] == "b"
    assert result["verification"]["confidence"] == "high"
    event = turn_events(client, "escalation")[0]
    assert event["accepted"] is True and event["before"] == "low" and event["after"] == "high"


def test_worse_or_equal_escalation_keeps_the_original_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_ESCALATION_ENABLED", "true")
    weak, strong = Fake("a", "tiny-1b", WEAK), Fake("b", "big-70b", WEAK)
    client = client_for(tmp_path, monkeypatch, weak, strong)
    result = chat(client, "conv-esc-same-1")
    assert (weak.calls, strong.calls) == (1, 1)  # exactly one extra attempt, never a loop
    assert result["content"] == WEAK and result["provider_id"] == "a"
    assert turn_events(client, "escalation")[0]["accepted"] is False


def test_escalation_endpoint_is_read_only_and_reflects_policy_and_counters(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_ESCALATION_ENABLED", "true")
    monkeypatch.delenv("MIA_ALLOW_PAID", raising=False)
    monkeypatch.setenv("MIA_ESCALATION_ALLOW_PAID", "true")
    monkeypatch.setenv("MIA_ESCALATION_MAX_COST_USD", "1")
    weak, strong = Fake("a", "tiny-1b", WEAK), Fake("b", "big-70b", GOOD)
    client = client_for(tmp_path, monkeypatch, weak, strong)
    info = client.get("/api/gateway/escalation").json()
    assert info["policy"]["enabled"] is True and info["policy"]["streaming"] == "not_supported"
    assert info["policy"]["allow_paid_requested"] is True
    assert info["policy"]["paid_escalation_effective"] is False  # route-level paid is off
    assert info["stats"]["attempted"] == 0
    chat(client, "conv-esc-stats-1")
    stats = client.get("/api/gateway/escalation").json()["stats"]
    assert stats["attempted"] == 1 and stats["accepted"] == 1
    assert client.get("/api/gateway/overview").json()["escalation"]["stats"]["accepted"] == 1
    assert client.post("/api/gateway/escalation", json={}).status_code == 405  # no write surface


def test_stats_count_skips_with_reasons():
    from mia.gateway.escalation import EscalationStats
    s = EscalationStats()
    s.record("skipped", "extra-attempt limit reached")
    s.record("failed")
    snap = s.snapshot()
    assert snap["skipped"] == 1 and snap["failed"] == 1 and snap["attempted"] == 1
    assert snap["skip_reasons"] == {"extra-attempt limit reached": 1}

