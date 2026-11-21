"""Bounded, opt-in quality escalation policy (MIA-GW-01).

Mia's gateway already falls back across candidates when a provider *fails*. This
module covers the other case: a provider *answered* but the deterministic
verifier judged the answer weak (empty, invalid structured output, heavily
hedged). It decides - without any I/O - whether one extra attempt on a stronger,
different model is allowed.

Design rules (all fail closed):

* Disabled by default. Nothing escalates unless the owner enables it.
* Never after side effects: if any tool ran or an approval is pending, a retry
  could repeat an action, so no escalation happens.
* At most ``max_extra_attempts`` additional completions (hard ceiling 2).
* Privacy: a sensitive request may only escalate to a local model.
* Cost: paid targets need both escalation-level and route-level paid permission,
  known prices, and an estimated cost within both caps. Unknown price = refused.
* Latency/tokens: the target's advertised latency and the retry's output tokens
  are capped.
* The target must differ from every model already tried and be demonstrably
  stronger (larger parameter hint, else a clearly higher reliability score).
  Parameter hints only order models that already passed eligibility checks; they
  never grant a capability.

Nothing here calls a provider. The caller performs the single extra completion
through the normal gateway (which still enforces budget reservations) and keeps
the original answer unless the new one is verifiably better.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace

from .contracts import ModelCandidate, ModelRequest, RoutePolicy
from .health import candidate_key
from .routing import estimate_cost_usd

HARD_MAX_EXTRA_ATTEMPTS = 2
DEFAULT_ELIGIBLE_CODES = frozenset({"empty_response", "invalid_structured_output", "low_confidence"})
_RELIABILITY_MARGIN = 0.1
_SIZE_RE = re.compile(r"(?<![0-9.])(\d{1,3}(?:\.\d)?)\s*b(?![a-z])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    enabled: bool = False
    max_extra_attempts: int = 1
    max_extra_latency_ms: float = 30_000.0
    max_extra_output_tokens: int = 2048
    allow_paid: bool = False
    max_extra_cost_usd: float = 0.0
    eligible_codes: frozenset[str] = DEFAULT_ELIGIBLE_CODES


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    escalate: bool
    reason: str
    target: ModelCandidate | None = None
    request: ModelRequest | None = None  # the bounded request to send to ``target``
    rejected: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "escalate": self.escalate,
            "reason": self.reason,
            "target": candidate_key(self.target) if self.target else None,
            "rejected": [{"candidate": k, "reason": r} for k, r in self.rejected],
        }


def _size_hint(model_id: str) -> float | None:
    match = _SIZE_RE.search(model_id)
    return float(match.group(1)) if match else None


def _finite_nonneg(value: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _stronger(target: ModelCandidate, used: ModelCandidate | None) -> bool:
    if used is None:
        return True
    t_size, u_size = _size_hint(target.model_id), _size_hint(used.model_id)
    if t_size is not None and u_size is not None:
        return t_size > u_size
    return target.reliability_score >= used.reliability_score + _RELIABILITY_MARGIN


def decide_escalation(
    *,
    policy: EscalationPolicy,
    confidence: str,
    issue_codes: Iterable[str],
    request: ModelRequest,
    candidates: Iterable[ModelCandidate],
    route_policy: RoutePolicy,
    used: ModelCandidate | None = None,
    tried: Iterable[str] = (),
    attempts_so_far: int = 0,
    had_side_effects: bool = False,
) -> EscalationDecision:
    """Return whether one extra, bounded attempt on a stronger model is allowed."""
    if not policy.enabled:
        return EscalationDecision(False, "escalation disabled")
    if had_side_effects:
        return EscalationDecision(False, "tools ran or approvals are pending; a retry could repeat an action")
    if confidence != "low":
        return EscalationDecision(False, f"verifier confidence is {confidence!r}, not 'low'")
    triggers = sorted(set(issue_codes) & policy.eligible_codes)
    if not triggers:
        return EscalationDecision(False, "no escalation-eligible verifier issue")
    cap = max(0, min(int(policy.max_extra_attempts), HARD_MAX_EXTRA_ATTEMPTS))
    if attempts_so_far >= cap:
        return EscalationDecision(False, "extra-attempt limit reached")
    if not _finite_nonneg(policy.max_extra_latency_ms) or policy.max_extra_output_tokens <= 0:
        return EscalationDecision(False, "invalid escalation limits")

    bounded = replace(
        request,
        tools=(),  # an escalation attempt never requests tool calls
        max_output_tokens=min(request.max_output_tokens, policy.max_extra_output_tokens),
    )
    input_tokens = bounded.estimated_input_tokens or max(1, len(bounded.prompt) // 4)
    needed = input_tokens + bounded.max_output_tokens
    seen = set(tried)
    if used is not None:
        seen.add(candidate_key(used))

    rejected: list[tuple[str, str]] = []
    viable: list[tuple[float, ModelCandidate]] = []
    paid_ok = (
        policy.allow_paid
        and route_policy.allow_paid
        and _finite_nonneg(policy.max_extra_cost_usd)
        and _finite_nonneg(route_policy.max_request_cost_usd)
    )
    cost_cap = min(policy.max_extra_cost_usd, route_policy.max_request_cost_usd) if paid_ok else 0.0
    for c in candidates:
        key = candidate_key(c)
        if key in seen:
            reason = "already tried for this turn"
        elif not c.enabled or not c.healthy:
            reason = "disabled or unhealthy"
        elif bounded.sensitive and not c.data_local:
            reason = "private request may only use a local model"
        elif bounded.required_capabilities - c.capabilities:
            reason = "lacks required capability"
        elif c.context_window < needed:
            reason = "context window too small"
        elif c.quota_remaining is not None and c.quota_remaining <= 0:
            reason = "quota exhausted"
        elif not _finite_nonneg(c.latency_ms) or c.latency_ms > policy.max_extra_latency_ms:
            reason = "slower than the escalation latency limit"
        elif not _stronger(c, used):
            reason = "not demonstrably stronger than the model already used"
        else:
            reason, cost = "", 0.0
            if not c.free_tier:
                est = estimate_cost_usd(c, bounded)
                if not paid_ok:
                    reason = "paid escalation not permitted"
                elif est is None:
                    reason = "price unknown"
                elif est > cost_cap:
                    reason = "over escalation cost cap"
                else:
                    cost = est
            if not reason:
                viable.append((cost, c))
                continue
        rejected.append((key, reason))

    if not viable:
        return EscalationDecision(False, "no eligible stronger model", rejected=tuple(rejected))
    # Free/local first, then cheapest, then the most reliable, then the lowest latency.
    viable.sort(key=lambda item: (item[0], -item[1].reliability_score, item[1].latency_ms, candidate_key(item[1])))
    target = viable[0][1]
    return EscalationDecision(
        True,
        "escalating after " + ", ".join(triggers),
        target=target,
        request=bounded,
        rejected=tuple(rejected),
    )


def escalation_policy_from_env() -> EscalationPolicy:
    """Read the opt-in policy from the environment. Invalid values fail closed."""

    def flag(name: str) -> bool:
        return os.getenv(name, "false").strip().lower() == "true"

    def number(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, "") or default)
        except ValueError:
            return default
        return value if math.isfinite(value) and value >= 0 else default

    return EscalationPolicy(
        enabled=flag("MIA_ESCALATION_ENABLED"),
        max_extra_attempts=int(number("MIA_ESCALATION_MAX_ATTEMPTS", 1)),
        max_extra_latency_ms=number("MIA_ESCALATION_MAX_LATENCY_MS", 30_000.0),
        max_extra_output_tokens=int(number("MIA_ESCALATION_MAX_OUTPUT_TOKENS", 2048)) or 2048,
        allow_paid=flag("MIA_ESCALATION_ALLOW_PAID"),
        max_extra_cost_usd=number("MIA_ESCALATION_MAX_COST_USD", 0.0),
    )
