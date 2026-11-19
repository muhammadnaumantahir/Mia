"""Deterministic, free-first routing policy with explicit paid-use controls."""

import math

from .contracts import ModelCandidate, ModelRequest, RoutePolicy, TokenUsage


def estimate_cost_usd(
    candidate: ModelCandidate,
    request: ModelRequest,
) -> float | None:
    """Estimate cost only when both input and output prices are known."""
    prices = (
        candidate.input_cost_per_million_usd,
        candidate.output_cost_per_million_usd,
    )
    if any(price is None or not math.isfinite(price) or price < 0 for price in prices):
        return None

    input_tokens = request.estimated_input_tokens or max(
        1, len(request.prompt) // 4
    )
    if input_tokens < 0 or request.max_output_tokens < 0:
        return None
    input_cost = (
        input_tokens * candidate.input_cost_per_million_usd / 1_000_000
    )
    output_cost = (
        request.max_output_tokens
        * candidate.output_cost_per_million_usd
        / 1_000_000
    )
    total = input_cost + output_cost
    return total if math.isfinite(total) else None


def actual_cost_usd(
    candidate: ModelCandidate,
    usage: TokenUsage | None,
    request: ModelRequest,
) -> float | None:
    """Cost of a finished completion.

    Free-tier routes cost 0. Paid routes use provider-reported tokens; with no
    reported usage they fall back to the worst-case estimate so spend is never
    under-counted. Returns None only if prices are invalid (never treated as 0).
    """
    if candidate.free_tier:
        return 0.0
    prices = (
        candidate.input_cost_per_million_usd,
        candidate.output_cost_per_million_usd,
    )
    if any(price is None or not math.isfinite(price) or price < 0 for price in prices):
        return None
    if usage is None:
        return estimate_cost_usd(candidate, request)
    if usage.input_tokens < 0 or usage.output_tokens < 0:
        return None
    total = (
        usage.input_tokens * candidate.input_cost_per_million_usd
        + usage.output_tokens * candidate.output_cost_per_million_usd
    ) / 1_000_000
    return total if math.isfinite(total) else None


def ineligibility_reason(
    candidate: ModelCandidate,
    request: ModelRequest,
    policy: RoutePolicy,
    *,
    needed_tokens: int,
    budget_is_valid: bool,
) -> tuple[str | None, float]:
    """Why a candidate cannot serve the request (None if it can) and its estimated cost."""
    if not candidate.enabled:
        return "disabled", 0.0
    if not candidate.healthy:
        return "unhealthy", 0.0
    missing = request.required_capabilities - candidate.capabilities
    if missing:
        return "missing capabilities: " + ", ".join(sorted(missing)), 0.0
    if candidate.context_window < needed_tokens:
        return "context window too small", 0.0
    if candidate.quota_remaining is not None and candidate.quota_remaining <= 0:
        return "quota exhausted", 0.0
    if candidate.free_tier:
        return None, 0.0
    if not policy.allow_paid or not budget_is_valid:
        return "paid route not permitted", 0.0
    estimated_cost = estimate_cost_usd(candidate, request)
    if estimated_cost is None:
        return "price unknown", 0.0
    if estimated_cost > policy.max_request_cost_usd:
        return "over per-request budget", estimated_cost
    return None, estimated_cost


def rank_candidates(
    candidates: list[ModelCandidate],
    request: ModelRequest,
    policy: RoutePolicy = RoutePolicy(),
) -> list[ModelCandidate]:
    """Return eligible candidates, cheapest and most reliable first.

    Free-tier status must be rechecked against current provider terms and quota
    at dispatch. Unknown prices are never treated as zero for paid routes.
    This function does not make requests or rotate credentials.
    """
    eligible: list[tuple[tuple[float, float, float], ModelCandidate]] = []
    input_tokens = request.estimated_input_tokens or max(1, len(request.prompt) // 4)
    if input_tokens < 0 or request.max_output_tokens < 0:
        return []
    needed_tokens = input_tokens + request.max_output_tokens
    budget_is_valid = math.isfinite(policy.max_request_cost_usd) and policy.max_request_cost_usd >= 0

    for candidate in candidates:
        reason, estimated_cost = ineligibility_reason(
            candidate,
            request,
            policy,
            needed_tokens=needed_tokens,
            budget_is_valid=budget_is_valid,
        )
        if reason is not None:
            continue
        sort_key = (
            estimated_cost,
            -max(0.0, min(1.0, candidate.reliability_score)),
            max(0.0, candidate.latency_ms),
        )
        eligible.append((sort_key, candidate))

    eligible.sort(key=lambda item: item[0])
    return [candidate for _, candidate in eligible]
