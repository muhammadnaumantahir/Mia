import pytest

from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.registry import ProviderRegistry
from mia.gateway.selection import (
    DecisionLog,
    RoutingMode,
    RoutingPreferences,
    select,
)
from mia.gateway.service import ModelGateway


def c(pid, mid, *, caps=("chat",), ctx=8192, free=True, local=False, rel=0.5, lat=1000.0,
      healthy=True, enabled=True, paid_prices=None):
    price = paid_prices or (None, None)
    return ModelCandidate(pid, mid, frozenset(caps), ctx, free, healthy, enabled,
                          reliability_score=rel, latency_ms=lat, data_local=local,
                          input_cost_per_million_usd=price[0],
                          output_cost_per_million_usd=price[1])


REQ = ModelRequest(task_type="chat", prompt="hello", max_output_tokens=100)
POOL = [
    c("remote", "fast", lat=200, rel=0.9, ctx=32000),
    c("local", "slow", local=True, lat=3000, rel=0.6, ctx=8192),
    c("remote", "big", lat=900, rel=0.7, ctx=128000),
]


def keys(ordered):
    return [f"{x.provider_id}/{x.model_id}" for x in ordered]


def test_default_mode_matches_original_free_first_order():
    ordered, decision = select(POOL, REQ, RoutePolicy())
    assert keys(ordered) == ["remote/fast", "remote/big", "local/slow"]
    assert decision.mode == "free_first" and decision.selected == "remote/fast"
    assert decision.reason.startswith("lowest cost")


def test_local_first_puts_local_ahead_then_keeps_base_order():
    ordered, d = select(POOL, REQ, RoutePolicy(), RoutingPreferences(mode=RoutingMode.LOCAL_FIRST))
    assert keys(ordered) == ["local/slow", "remote/fast", "remote/big"]
    assert d.selected == "local/slow"


def test_lowest_latency_and_best_quality_modes():
    ordered, _ = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(mode=RoutingMode.LOWEST_LATENCY))
    assert keys(ordered) == ["remote/fast", "remote/big", "local/slow"]
    ordered, d = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(mode=RoutingMode.BEST_QUALITY))
    assert keys(ordered)[0] == "remote/big"  # largest context, no preferences set
    ordered, _ = select(POOL, REQ, RoutePolicy(), RoutingPreferences(
        mode=RoutingMode.BEST_QUALITY, preferences={"local/slow": 5}))
    assert keys(ordered)[0] == "local/slow"


def test_preference_breaks_ties_between_equally_cheap_models():
    ordered, _ = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(preferences={"remote/big": 3}))
    assert keys(ordered)[0] == "remote/big"


def test_pin_goes_first_and_fallbacks_remain_by_default():
    prefs = RoutingPreferences(pinned="local/slow")
    ordered, d = select(POOL, REQ, RoutePolicy(), prefs)
    assert keys(ordered) == ["local/slow", "remote/fast", "remote/big"]
    assert d.reason.startswith("pinned by owner") and d.overrides == ()
    ordered, _ = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(pinned="local/slow", pin_allows_fallback=False))
    assert keys(ordered) == ["local/slow"]


def test_pin_overridden_when_it_lacks_mandatory_capability_with_explanation():
    req = ModelRequest(task_type="agent", prompt="x", required_capabilities=frozenset({"tools"}))
    pool = [c("a", "plain"), c("b", "tooly", caps=("chat", "tools"))]
    ordered, d = select(pool, req, RoutePolicy(), RoutingPreferences(pinned="a/plain"))
    assert keys(ordered) == ["b/tooly"]
    assert d.overrides == ("pinned model a/plain not used: missing capabilities: tools",)
    assert ("a/plain", "missing capabilities: tools") in d.rejected


def test_manual_mode_uses_only_the_pin_and_never_silently_falls_back():
    ordered, d = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(mode=RoutingMode.MANUAL, pinned="remote/big"))
    assert keys(ordered) == ["remote/big"]
    ordered, d = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(mode=RoutingMode.MANUAL, pinned="gone/model"))
    assert ordered == [] and d.selected is None
    assert any("manual mode" in o for o in d.overrides)
    ordered, d = select(POOL, REQ, RoutePolicy(), RoutingPreferences(mode=RoutingMode.MANUAL))
    assert ordered == []


def test_sensitive_requests_stay_local_unless_owner_allows_remote():
    sensitive = ModelRequest(task_type="chat", prompt="my diary", sensitive=True)
    ordered, d = select(POOL, sensitive, RoutePolicy())
    assert keys(ordered) == ["local/slow"]
    assert ("remote/fast", "privacy: remote models not permitted for this request") in d.rejected
    ordered, _ = select(POOL, sensitive, RoutePolicy(),
                        RoutingPreferences(allow_remote_for_sensitive=True))
    assert len(ordered) == 3
    # No local model available: nothing is chosen rather than leaking to remote.
    ordered, d = select([POOL[0], POOL[2]], sensitive, RoutePolicy())
    assert ordered == [] and d.reason == "no eligible model"


def test_local_only_mode_blocks_all_remote_even_when_pinned():
    prefs = RoutingPreferences(local_only=True, pinned="remote/fast")
    ordered, d = select(POOL, REQ, RoutePolicy(), prefs)
    assert keys(ordered) == ["local/slow"]
    assert ("remote/fast", "local-only mode") in d.rejected
    assert "pinned model remote/fast not used: local-only mode" in d.overrides


def test_paid_models_still_require_policy_and_decision_explains_rejection():
    paid = c("pay", "gpt", free=False, paid_prices=(1.0, 2.0))
    ordered, d = select([paid, POOL[1]], REQ, RoutePolicy())
    assert keys(ordered) == ["local/slow"]
    assert ("pay/gpt", "paid route not permitted") in d.rejected
    ordered, _ = select([paid, POOL[1]], REQ, RoutePolicy(allow_paid=True, max_request_cost_usd=1.0))
    assert keys(ordered) == ["local/slow", "pay/gpt"]  # free before paid


def test_hint_narrows_and_reorders_only_in_default_mode_without_pin():
    hint = [POOL[2], POOL[1]]  # big, slow ; drops fast
    ordered, _ = select(POOL, REQ, RoutePolicy(), hint=hint)
    assert keys(ordered) == ["remote/big", "local/slow"]
    ordered, _ = select(POOL, REQ, RoutePolicy(),
                        RoutingPreferences(mode=RoutingMode.LOCAL_FIRST), hint=hint)
    assert keys(ordered) == ["local/slow", "remote/big"]  # mode order wins, hint narrows
    ordered, _ = select(POOL, REQ, RoutePolicy(), RoutingPreferences(pinned="remote/fast"),
                        hint=hint)
    assert keys(ordered)[0] == "remote/fast"  # pin survives a hint that omits it


def test_hint_cannot_reintroduce_rejected_candidates():
    bad = c("x", "off", enabled=False)
    ordered, _ = select([POOL[0], bad], REQ, RoutePolicy(), hint=[bad, POOL[0]])
    assert keys(ordered) == ["remote/fast"]


def test_decision_contains_no_prompt_content_and_log_is_bounded():
    secret = ModelRequest(task_type="chat", prompt="TOP SECRET PROMPT", sensitive=False,
                          messages=({"role": "user", "content": "TOP SECRET MESSAGE"},))
    _, d = select(POOL, secret, RoutePolicy())
    assert "TOP SECRET" not in repr(d.to_dict())
    log = DecisionLog(capacity=3)
    for i in range(5):
        log.add(select(POOL, ModelRequest(task_type=f"t{i}", prompt="x"), RoutePolicy())[1])
    assert [x.task_type for x in log.recent()] == ["t4", "t3", "t2"]


def test_invalid_token_limits_select_nothing():
    bad = ModelRequest(task_type="chat", prompt="x", max_output_tokens=-1)
    ordered, d = select(POOL, bad, RoutePolicy())
    assert ordered == [] and d.reason == "no eligible model"


def test_gateway_validates_preferences_and_records_decisions():
    gw = ModelGateway(ProviderRegistry())
    with pytest.raises(ValueError):
        gw.set_preferences(RoutingPreferences(pinned="no-slash"))
    with pytest.raises(ValueError):
        gw.set_preferences(RoutingPreferences(preferences={"a/b": 99}))
    gw.set_preferences(RoutingPreferences(mode=RoutingMode.LOCAL_FIRST))
    assert gw.preferences.mode is RoutingMode.LOCAL_FIRST
    gw._rank(POOL, REQ, RoutePolicy(), None)
    assert gw.decisions.recent(1)[0].mode == "local_first"


def test_persistent_decision_log_survives_restart_and_remains_bounded(tmp_path):
    path = tmp_path / "routing.sqlite3"
    first = DecisionLog(capacity=3, database_path=path)
    decisions = [
        select(POOL, ModelRequest(task_type=f"task-{i}", prompt="x"), RoutePolicy())[1]
        for i in range(5)
    ]
    for decision in decisions:
        first.add(decision)

    restarted = DecisionLog(capacity=3, database_path=path)
    assert [item.task_type for item in restarted.recent()] == ["task-4", "task-3", "task-2"]
    assert "prompt" not in repr(restarted.recent()[0].to_dict())
    assert "TOP SECRET" not in repr(restarted.recent())
    assert restarted.recent(0) == []
