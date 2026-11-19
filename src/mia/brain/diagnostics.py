"""Privacy-safe turn traces for the intelligence diagnostics view (SPS-05 traceability).

A trace records *what the Brain decided and why* - model selection and reasons,
retries/fallbacks, plan and step state, tool outcomes, verification - and never
prompts, replies, tool arguments/outputs, credentials or memory contents.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"\b(sk|pk|ghp|github_pat|xox[bp]|AIza)[-_A-Za-z0-9]{10,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
)


def scrub(text: Any, limit: int = 300) -> str:
    value = str(text if text is not None else "")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[redacted]", value)
    return value[:limit]


@dataclass(slots=True)
class TurnTrace:
    interface: str
    conversation_id: str
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    _t0: float = field(default_factory=time.perf_counter)
    intent: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    route: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    outcome: str = "running"
    duration_ms: float = 0.0

    def event(self, kind: str, **data: Any) -> None:
        self.events.append({
            "t_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "event": kind,
            **{k: (scrub(v) if isinstance(v, str) else v) for k, v in data.items()},
        })

    def finish(self, outcome: str, error: str = "") -> None:
        self.outcome = outcome
        self.error = scrub(error)
        self.duration_ms = round((time.perf_counter() - self._t0) * 1000, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id, "interface": self.interface,
            "conversation_id": self.conversation_id, "started_at": self.started_at,
            "outcome": self.outcome, "duration_ms": self.duration_ms, "error": self.error,
            "intent": self.intent, "context": self.context, "route": self.route,
            "execution": self.execution, "plan": self.plan, "tools": self.tools,
            "events": self.events, "verification": self.verification,
        }


class DiagnosticsLog:
    """Bounded in-memory ring of recent turn traces (nothing sensitive is stored)."""

    def __init__(self, capacity: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def add(self, trace: TurnTrace) -> None:
        with self._lock:
            self._items.append(trace.to_dict())

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._items)[-max(1, min(limit, 200)):][::-1]

    def get(self, turn_id: str) -> dict[str, Any] | None:
        with self._lock:
            for item in reversed(self._items):
                if item["turn_id"] == turn_id:
                    return item
        return None

    def summary(self) -> dict[str, Any]:
        with self._lock:
            items = list(self._items)
        outcomes: dict[str, int] = {}
        models: dict[str, int] = {}
        fallbacks = refusals = 0
        for item in items:
            outcomes[item["outcome"]] = outcomes.get(item["outcome"], 0) + 1
            selected = (item.get("execution") or {}).get("provider_model")
            if selected:
                models[selected] = models.get(selected, 0) + 1
            attempted = (item.get("execution") or {}).get("attempted") or []
            if len(attempted) > 1:
                fallbacks += 1
            refusals += sum(1 for t in item.get("tools", []) if t.get("status") == "refused")
        return {"turns": len(items), "outcomes": outcomes, "models": models,
                "turns_with_fallback": fallbacks, "refused_tool_calls": refusals}
