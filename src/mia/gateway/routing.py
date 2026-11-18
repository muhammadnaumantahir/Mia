"""Deterministic, free-first routing policy with explicit paid-use controls."""

import math

from .contracts import ModelCandidate, ModelRequest, RoutePolicy


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
    input_tokens = request.estimated_input_tokens or max(
        1, len(request.prompt) // 4
    )
    if input_tokens < 0 or request.max_output_tokens < 0:
        return []
    needed_tokens = input_tokens + request.max_output_tokens
    budget_is_valid = (
        math.isfinite(policy.max_request_cost_usd)
        and policy.max_request_cost_usd >= 0
    )

    for candidate in candidates:
        if not candidate.enabled or not candidate.healthy:
            continue
        if not request.required_capabilities.issubset(candidate.capabilities):
            continue
        if candidate.context_window < needed_tokens:
            continue
        if candidate.quota_remaining is not None and candidate.quota_remaining <= 0:
            continue

        if candidate.free_tier:
            estimated_cost = 0.0
        else:
            if not policy.allow_paid or not budget_is_valid:
                continue
            estimated_cost = estimate_cost_usd(candidate, request)
            if estimated_cost is None:
                continue
            if estimated_cost > policy.max_request_cost_usd:
                continue

        sort_key = (
            estimated_cost,
            -max(0.0, min(1.0, candidate.reliability_score)),
            max(0.0, candidate.latency_ms),
        )
        eligible.append((sort_key, candidate))

    eligible.sort(key=lambda item: item[0])
    return [candidate for _, candidate in eligible]
