"""Task-aware, explainable model routing (SPS-07 Adaptation Core).

Builds on the existing free-first ``rank_candidates`` policy. It adds task
profiles, verified-capability checks, user preferences and a recorded reason for
every selection and rejection. If nothing is suitable it raises
``NoSuitableModel`` with an explanation instead of silently using a poor fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.health import CandidateHealthTracker, candidate_key
from mia.gateway.language import detect_language
from mia.gateway.routing import estimate_cost_usd, rank_candidates

from .capabilities import CapabilityRegistry, TaskProfile, profile_for, size_hint_billions

# Free-form conversational categories where language fluency matters more than speed.
_LANGUAGE_SENSITIVE = frozenset({"conversation", "simple", "summarization"})


class NoSuitableModel(RuntimeError):
    """No available model satisfies the task; carries a human-readable explanation."""

    def __init__(self, message: str, rejected: list[tuple[str, str]]) -> None:
        super().__init__(message)
        self.rejected = rejected


@dataclass(frozen=True, slots=True)
class RoutingPreferences:
    preferred_providers: tuple[str, ...] = ()
    preferred_models: tuple[str, ...] = ()
    local_only: bool = False
    # Optional ceiling on acceptable latency (ms) for light tasks.
    max_latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class RouteDecision:
    category: str
    required_capabilities: frozenset[str]
    ordered: tuple[ModelCandidate, ...]
    reasons: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    context_needed: int
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> ModelCandidate:
        return self.ordered[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "required_capabilities": sorted(self.required_capabilities),
            "selected": candidate_key(self.selected),
            "fallback_order": [candidate_key(c) for c in self.ordered[1:]],
            "reasons": list(self.reasons),
            "rejected": [{"candidate": k, "reason": r} for k, r in self.rejected],
            "context_needed": self.context_needed,
        }


class BrainRouter:
    def __init__(
        self,
        capabilities: CapabilityRegistry | None = None,
        health: CandidateHealthTracker | None = None,
    ) -> None:
        self.capabilities = capabilities or CapabilityRegistry()
        self.health = health

    def _score(
        self,
        c: ModelCandidate,
        profile: TaskProfile,
        prefs: RoutingPreferences,
        request: ModelRequest,
    ) -> tuple[float, list[str]]:
        score = 0.0
        notes: list[str] = []
        if profile.prefer_local and c.data_local:
            score += 2.0
            notes.append("local execution preferred")
        if c.provider_id in prefs.preferred_providers or c.model_id in prefs.preferred_models:
            score += 3.0
            notes.append("matches user preference")
        score += max(0.0, min(1.0, c.reliability_score)) * 2.0
        size = size_hint_billions(c.model_id)
        language = detect_language(request.prompt)
        preferred = c.provider_id in prefs.preferred_providers or c.model_id in prefs.preferred_models
        # An explicit owner preference always beats this heuristic.
        if (language != "en" and not preferred and size is not None
                and profile.category in _LANGUAGE_SENSITIVE):
            # Small models are usually weak in Urdu/Roman Urdu: do not reward them for being
            # small, and mildly prefer larger ones. Ordering only; nothing is excluded.
            if size < 4:
                score -= 3.5
                notes.append(f"small model deprioritised for {language.replace('_', ' ')} input")
            elif size >= 7:
                score += 0.5
                notes.append(f"larger model preferred for {language.replace('_', ' ')} input")
        if profile.weight == "light":
            score += max(0.0, 2.0 - c.latency_ms / 5000.0)
            if size is not None and size <= 8:
                score += 1.0
                notes.append("lightweight model suits a simple task")
        elif profile.weight == "strong":
            score += min(2.0, c.context_window / 32768.0)
            if size is not None and size >= 7:
                score += 1.0
                notes.append("larger model suits demanding task")
        return score, notes

    def route(
        self,
        candidates: list[ModelCandidate],
        request: ModelRequest,
        *,
        task_type: str | None = None,
        policy: RoutePolicy = RoutePolicy(),
        preferences: RoutingPreferences = RoutingPreferences(),
    ) -> RouteDecision:
        profile = profile_for(task_type or request.task_type)
        required = frozenset(request.required_capabilities | profile.required_capabilities)
        input_tokens = request.estimated_input_tokens or max(1, len(request.prompt) // 4)
        needed = input_tokens + request.max_output_tokens
        min_context = max(profile.min_context, needed)

        rejected: list[tuple[str, str]] = []
        viable: list[ModelCandidate] = []
        for c in candidates:
            key = candidate_key(c)
            if not c.enabled:
                rejected.append((key, "disabled"))
            elif not c.healthy:
                rejected.append((key, "provider reports unhealthy"))
            elif self.health is not None and self.health.is_blocked(c):
                rejected.append((key, "rate limited; waiting for provider-advised delay"))
            elif preferences.local_only and not c.data_local:
                rejected.append((key, "non-local model excluded by local-only preference"))
            else:
                view = self.capabilities.view(c)
                missing = required - view.declared
                refuted = required & view.refuted
                if missing:
                    rejected.append((key, f"lacks capability: {', '.join(sorted(missing))}"))
                elif refuted:
                    rejected.append(
                        (key, f"capability failed at runtime: {', '.join(sorted(refuted))}")
                    )
                elif c.context_window < min_context:
                    rejected.append(
                        (key, f"context window {c.context_window} < {min_context} needed")
                    )
                elif (
                    profile.weight == "light"
                    and preferences.max_latency_ms is not None
                    and c.latency_ms > preferences.max_latency_ms
                ):
                    rejected.append((key, "slower than the configured latency limit"))
                else:
                    viable.append(c)

        eligible = rank_candidates(
            viable,
            ModelRequest(
                task_type=request.task_type,
                prompt=request.prompt,
                estimated_input_tokens=input_tokens,
                max_output_tokens=request.max_output_tokens,
                required_capabilities=required,
            ),
            policy,
        )
        eligible_keys = {candidate_key(c) for c in eligible}
        for c in viable:
            if candidate_key(c) not in eligible_keys:
                rejected.append((candidate_key(c), "excluded by cost/quota policy"))

        if not eligible:
            detail = "; ".join(f"{k}: {r}" for k, r in rejected) or "no models discovered"
            raise NoSuitableModel(
                f"No suitable model for {profile.category} task "
                f"(needs {', '.join(sorted(required))}, >= {min_context} context). {detail}",
                rejected,
            )

        scored: list[tuple[tuple[float, float], ModelCandidate, list[str]]] = []
        for c in eligible:
            cost = estimate_cost_usd(c, request) if not c.free_tier else 0.0
            score, notes = self._score(c, profile, preferences, request)
            scored.append(((cost if cost is not None else float("inf"), -score), c, notes))
        scored.sort(key=lambda item: item[0])
        ordered = tuple(c for _, c, _ in scored)
        first_notes = scored[0][2]
        reasons = [
            f"task category: {profile.category}",
            f"selected {candidate_key(ordered[0])} from {len(ordered)} eligible candidate(s)",
            "free/local-first cost ordering applied before quality scoring"
            if ordered[0].free_tier
            else "paid route allowed by explicit budget policy",
            *first_notes,
        ]
        return RouteDecision(
            profile.category, required, ordered, tuple(reasons), tuple(rejected), min_context
        )
