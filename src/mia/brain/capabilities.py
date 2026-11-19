"""Task profiles and the model capability registry (declared vs runtime-verified).

SPS-01 (capability definitions) / SPS-07 (adaptation of routing).

Capabilities come from provider configuration and discovery (declared). The
registry additionally records runtime observations so a model that claims a
capability but demonstrably fails at it is treated as lacking it. A model name
never grants a capability.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

from mia.gateway.contracts import ModelCandidate
from mia.gateway.health import candidate_key

TASK_CATEGORIES = (
    "conversation",
    "reasoning",
    "coding",
    "research",
    "summarization",
    "classification",
    "planning",
    "extraction",
    "tool",
    "simple",
)

# Legacy task_type labels already used by the UI/API map onto categories.
_TASK_ALIASES = {
    "chat": "conversation",
    "general": "conversation",
    "code": "coding",
    "summarize": "summarization",
    "summary": "summarization",
    "classify": "classification",
    "plan": "planning",
    "extract": "extraction",
    "structured": "extraction",
    "tools": "tool",
    "tool_use": "tool",
    "light": "simple",
}


def normalise_category(task_type: str | None) -> str:
    value = (task_type or "").strip().lower()
    if value in TASK_CATEGORIES:
        return value
    return _TASK_ALIASES.get(value, "conversation")


@dataclass(frozen=True, slots=True)
class TaskProfile:
    category: str
    required_capabilities: frozenset[str] = frozenset({"chat"})
    min_context: int = 2048
    # "light": prefer fast/small; "strong": prefer capable/large-context.
    weight: str = "balanced"
    prefer_local: bool = True
    default_output_tokens: int = 1024


TASK_PROFILES: dict[str, TaskProfile] = {
    "conversation": TaskProfile("conversation", min_context=2048, weight="balanced"),
    "simple": TaskProfile("simple", min_context=1024, weight="light", default_output_tokens=256),
    "reasoning": TaskProfile("reasoning", min_context=8192, weight="strong",
                             default_output_tokens=2048),
    "coding": TaskProfile("coding", min_context=8192, weight="strong",
                          default_output_tokens=2048),
    "research": TaskProfile("research", min_context=8192, weight="strong",
                            default_output_tokens=2048),
    "summarization": TaskProfile("summarization", min_context=4096, weight="balanced"),
    "classification": TaskProfile("classification", min_context=1024, weight="light",
                                  default_output_tokens=128),
    "planning": TaskProfile("planning", min_context=8192, weight="strong",
                            default_output_tokens=1600),
    "extraction": TaskProfile("extraction", min_context=4096, weight="balanced",
                              default_output_tokens=1024),
    "tool": TaskProfile("tool", frozenset({"chat", "tools"}), min_context=4096,
                        weight="balanced"),
}


def profile_for(task_type: str | None) -> TaskProfile:
    return TASK_PROFILES[normalise_category(task_type)]


_SIZE_RE = re.compile(r"(?<![0-9.])(\d{1,3}(?:\.\d)?)\s*b(?![a-z])", re.IGNORECASE)


def size_hint_billions(model_id: str) -> float | None:
    """Parameter-count hint parsed from a model id. ORDERING HINT ONLY.

    It is never used to grant a capability; it only breaks ties between models
    that are already eligible when a task prefers a lighter or stronger model.
    """
    match = _SIZE_RE.search(model_id)
    return float(match.group(1)) if match else None


@dataclass(slots=True)
class _Observation:
    successes: int = 0
    failures: int = 0
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityView:
    declared: frozenset[str]
    verified: frozenset[str]
    refuted: frozenset[str]

    @property
    def effective(self) -> frozenset[str]:
        return self.declared - self.refuted


@dataclass(slots=True)
class CapabilityRegistry:
    """Runtime capability evidence per model. Thread-safe, in-memory."""

    refute_after_failures: int = 2
    _obs: dict[tuple[str, str], _Observation] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, candidate: ModelCandidate, capability: str, ok: bool, error: str = "") -> None:
        key = (candidate_key(candidate), capability)
        with self._lock:
            obs = self._obs.setdefault(key, _Observation())
            if ok:
                obs.successes += 1
                obs.failures = 0  # a success clears a refutation streak
            else:
                obs.failures += 1
                obs.last_error = error[:200]

    def view(self, candidate: ModelCandidate) -> CapabilityView:
        ck = candidate_key(candidate)
        verified: set[str] = set()
        refuted: set[str] = set()
        with self._lock:
            for (key, capability), obs in self._obs.items():
                if key != ck:
                    continue
                if obs.successes > 0 and obs.failures == 0:
                    verified.add(capability)
                if obs.failures >= self.refute_after_failures and obs.successes == 0:
                    refuted.add(capability)
        return CapabilityView(candidate.capabilities, frozenset(verified), frozenset(refuted))

    def effective(self, candidate: ModelCandidate) -> frozenset[str]:
        return self.view(candidate).effective

    def snapshot(self) -> list[dict[str, object]]:
        with self._lock:
            return [
                {
                    "candidate": key,
                    "capability": capability,
                    "successes": obs.successes,
                    "failures": obs.failures,
                    "last_error": obs.last_error,
                }
                for (key, capability), obs in sorted(self._obs.items())
            ]
