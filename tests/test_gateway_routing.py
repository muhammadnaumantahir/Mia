from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.routing import rank_candidates


def candidate(provider, model, *, free=True, healthy=True, quota=100, score=0.8):
    return ModelCandidate(
        provider_id=provider,
        model_id=model,
        capabilities=frozenset({"chat", "coding"}),
        context_window=16_000,
        free_tier=free,
        healthy=healthy,
        enabled=True,
        reliability_score=score,
        quota_remaining=quota,
        input_cost_per_million_usd=1.0,
        output_cost_per_million_usd=2.0,
    )


def test_free_first_and_reliability_order():
    request = ModelRequest(task_type="coding", prompt="test", required_capabilities=frozenset({"coding"}))
    options = [
        candidate("paid", "paid-model", free=False),
        candidate("free-low", "low", score=0.5),
        candidate("free-high", "high", score=0.9),
    ]
    ranked = rank_candidates(options, request)
    assert [item.provider_id for item in ranked] == ["free-high", "free-low"]


def test_paid_requires_explicit_permission_and_budget():
    request = ModelRequest(
        task_type="coding", prompt="test", estimated_input_tokens=1000,
        max_output_tokens=1000, required_capabilities=frozenset({"coding"}),
    )
    paid = candidate("paid", "model", free=False)
    assert rank_candidates([paid], request) == []
    assert rank_candidates([paid], request, RoutePolicy(allow_paid=True, max_request_cost_usd=0.01)) == [paid]
    assert rank_candidates([paid], request, RoutePolicy(allow_paid=True, max_request_cost_usd=0.001)) == []


def test_unhealthy_or_depleted_candidates_are_excluded():
    request = ModelRequest(task_type="chat", prompt="hi")
    options = [
        candidate("down", "down", healthy=False),
        candidate("empty", "empty", quota=0),
        candidate("ready", "ready"),
    ]
    assert [item.provider_id for item in rank_candidates(options, request)] == ["ready"]
