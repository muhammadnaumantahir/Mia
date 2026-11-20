"""Preference-aware model selection with an auditable decision record.

``rank_candidates`` decides who is *eligible* (capabilities, context, quota, cost).
This module decides the *order* according to the owner's routing mode, honours a
pinned model, enforces privacy restrictions, and records why.

Decision records hold provider/model identifiers, task category, capability
names and reasons only. They never contain prompt text, messages, tool
arguments, URLs or credentials.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from enum import StrEnum

from .contracts import ModelCandidate, ModelRequest, RoutePolicy
from .routing import ineligibility_reason


class RoutingMode(StrEnum):
    FREE_FIRST = "free_first"  # cheapest, then most reliable, then fastest (original behaviour)
    LOCAL_FIRST = "local_first"  # models whose data stays local first
    BEST_QUALITY = "best_quality"  # heuristic proxy, see _quality_key
    LOWEST_LATENCY = "lowest_latency"
    MANUAL = "manual"  # only the pinned model


@dataclass(frozen=True, slots=True)
class RoutingPreferences:
    mode: RoutingMode = RoutingMode.FREE_FIRST
    pinned: str | None = None  # "provider/model"
    pin_allows_fallback: bool = True
    local_only: bool = False  # never use a remote model
    allow_remote_for_sensitive: bool = False  # sensitive requests stay local unless true
    preferences: dict[str, int] = field(default_factory=dict)  # "provider/model" -> -10..10


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    task_type: str
    mode: str
    required_capabilities: tuple[str, ...]
    selected: str | None
    reason: str
    ranked: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]
    overrides: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "task_type": self.task_type,
            "mode": self.mode,
            "required_capabilities": list(self.required_capabilities),
            "selected": self.selected,
            "reason": self.reason,
            "ranked": list(self.ranked),
            "rejected": [{"model": k, "reason": r} for k, r in self.rejected],
            "overrides": list(self.overrides),
            "created_at": self.created_at,
        }


class DecisionLog:
    """Bounded route-decision history, optionally persisted without prompt content."""

    def __init__(
        self,
        capacity: int = 100,
        database_path: str | Path | None = None,
    ) -> None:
        self.capacity = max(1, min(int(capacity), 10_000))
        self.database_path = Path(database_path) if database_path is not None else None
        self._items: deque[RoutingDecision] = deque(maxlen=self.capacity)
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as db:
                db.execute(
                    """
                    CREATE TABLE IF NOT EXISTS routing_decisions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        decision_json TEXT NOT NULL
                    )
                    """
                )
                db.execute(
                    "CREATE INDEX IF NOT EXISTS routing_decisions_time "
                    "ON routing_decisions(created_at)"
                )

    def _connect(self):
        if self.database_path is None:
            raise RuntimeError("persistent decision storage is not configured")
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _decode(payload: str) -> RoutingDecision:
        data = json.loads(payload)
        return RoutingDecision(
            task_type=str(data["task_type"]),
            mode=str(data["mode"]),
            required_capabilities=tuple(str(v) for v in data.get("required_capabilities", [])),
            selected=data.get("selected"),
            reason=str(data["reason"]),
            ranked=tuple(str(v) for v in data.get("ranked", [])),
            rejected=tuple(
                (str(item["model"]), str(item["reason"]))
                for item in data.get("rejected", [])
            ),
            overrides=tuple(str(v) for v in data.get("overrides", [])),
            created_at=str(data["created_at"]),
        )

    def add(self, decision: RoutingDecision) -> None:
        self._items.append(decision)
        if self.database_path is not None:
            payload = json.dumps(
                decision.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            with self._connect() as db:
                db.execute(
                    "INSERT INTO routing_decisions(created_at, decision_json) VALUES (?, ?)",
                    (decision.created_at, payload),
                )
                db.execute(
                    "DELETE FROM routing_decisions WHERE id NOT IN "
                    "(SELECT id FROM routing_decisions ORDER BY id DESC LIMIT ?)",
                    (self.capacity,),
                )

    def recent(self, limit: int = 20) -> list[RoutingDecision]:
        safe_limit = max(0, min(int(limit), 10_000))
        if self.database_path is not None:
            with self._connect() as db:
                rows = db.execute(
                    "SELECT decision_json FROM routing_decisions "
                    "ORDER BY id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            return [self._decode(row["decision_json"]) for row in rows]
        return list(self._items)[-safe_limit:][::-1] if safe_limit else []


def _key(candidate: ModelCandidate) -> str:
    return f"{candidate.provider_id}/{candidate.model_id}"


def _reliability(candidate: ModelCandidate) -> float:
    return max(0.0, min(1.0, candidate.reliability_score))


def _pref(candidate: ModelCandidate, prefs: RoutingPreferences) -> int:
    return prefs.preferences.get(_key(candidate), 0)


def _quality_key(candidate: ModelCandidate, prefs: RoutingPreferences) -> tuple[float, ...]:
    # There is no benchmark data, so quality is a heuristic proxy: owner preference,
    # then larger context window, then observed reliability.
    return (-_pref(candidate, prefs), -candidate.context_window, -_reliability(candidate))


def select(
    candidates: Iterable[ModelCandidate],
    request: ModelRequest,
    policy: RoutePolicy,
    prefs: RoutingPreferences | None = None,
    *,
    hint: Iterable[ModelCandidate] | None = None,
) -> tuple[list[ModelCandidate], RoutingDecision]:
    """Order eligible candidates and explain the choice.

    ``hint`` is a task-aware order from the caller. It can narrow the list, and
    reorder it only in the default mode without a pin; it never reintroduces a
    rejected candidate.
    """
    prefs = prefs or RoutingPreferences()
    candidates = list(candidates)
    rejected: list[tuple[str, str]] = []
    overrides: list[str] = []
    eligible: list[tuple[ModelCandidate, float]] = []

    input_tokens = request.estimated_input_tokens or max(1, len(request.prompt) // 4)
    invalid = input_tokens < 0 or request.max_output_tokens < 0
    needed = input_tokens + request.max_output_tokens
    budget_ok = math.isfinite(policy.max_request_cost_usd) and policy.max_request_cost_usd >= 0

    remote_barred = prefs.local_only or (request.sensitive and not prefs.allow_remote_for_sensitive)
    for candidate in candidates:
        if invalid:
            rejected.append((_key(candidate), "invalid token limits"))
            continue
        reason, cost = ineligibility_reason(
            candidate, request, policy, needed_tokens=needed, budget_is_valid=budget_ok
        )
        if reason is None and remote_barred and not candidate.data_local:
            reason = (
                "privacy: remote models not permitted for this request"
                if not prefs.local_only
                else "local-only mode"
            )
        if reason is not None:
            rejected.append((_key(candidate), reason))
        else:
            eligible.append((candidate, cost))

    def base_key(item: tuple[ModelCandidate, float]) -> tuple[float, ...]:
        candidate, cost = item
        return (cost, -_pref(candidate, prefs), -_reliability(candidate), max(0.0, candidate.latency_ms))

    eligible.sort(key=base_key)  # stable base order used by every mode
    mode = prefs.mode
    if mode is RoutingMode.LOCAL_FIRST:
        eligible.sort(key=lambda item: 0 if item[0].data_local else 1)
    elif mode is RoutingMode.BEST_QUALITY:
        eligible.sort(key=lambda item: _quality_key(item[0], prefs))
    elif mode is RoutingMode.LOWEST_LATENCY:
        eligible.sort(key=lambda item: max(0.0, item[0].latency_ms))
    ordered = [candidate for candidate, _ in eligible]

    pinned_key = prefs.pinned
    pinned = next((c for c in ordered if _key(c) == pinned_key), None) if pinned_key else None
    if pinned_key and pinned is None:
        why = next((r for k, r in rejected if k == pinned_key), "not available")
        overrides.append(f"pinned model {pinned_key} not used: {why}")

    if mode is RoutingMode.MANUAL:
        ordered = [pinned] if pinned is not None else []
        if pinned is None:
            overrides.append("manual mode: no usable pinned model, nothing selected")
    else:
        if hint is not None:
            allowed = {_key(c) for c in hint}
            if mode is RoutingMode.FREE_FIRST and pinned is None:
                by_key = {_key(c): c for c in ordered}
                ordered = [by_key[_key(c)] for c in hint if _key(c) in by_key]
            else:
                ordered = [c for c in ordered if _key(c) in allowed or c is pinned]
        if pinned is not None:
            rest = [c for c in ordered if c is not pinned]
            ordered = [pinned] + (rest if prefs.pin_allows_fallback else [])

    selected = _key(ordered[0]) if ordered else None
    if selected is None:
        reason = "no eligible model"
    elif pinned is not None and ordered[0] is pinned:
        reason = f"pinned by owner ({mode.value} mode)"
    else:
        reason = {
            RoutingMode.FREE_FIRST: "lowest cost, then most reliable and fastest",
            RoutingMode.LOCAL_FIRST: "local models first, then lowest cost",
            RoutingMode.BEST_QUALITY: "preference, then context window and reliability",
            RoutingMode.LOWEST_LATENCY: "lowest measured latency",
            RoutingMode.MANUAL: "manual selection",
        }[mode]
    decision = RoutingDecision(
        task_type=request.task_type,
        mode=mode.value,
        required_capabilities=tuple(sorted(request.required_capabilities)),
        selected=selected,
        reason=reason,
        ranked=tuple(_key(c) for c in ordered),
        rejected=tuple(rejected),
        overrides=tuple(overrides),
        created_at=datetime.now(UTC).isoformat(),
    )
    return ordered, decision
