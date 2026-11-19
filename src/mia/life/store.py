"""Durable curiosity backlog, activity journal and owner controls (SQLite).

Everything here is evidence: a journal entry exists only for a cycle that really
started, outcomes are limited to honest values, and absent measurements are
stored as NULL (unavailable), never as zero.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ITEM_STATES = frozenset(
    {"queued", "in_progress", "deferred", "done", "rejected", "dropped", "blocked"}
)
OPEN_STATES = frozenset({"queued", "in_progress", "deferred", "blocked"})
ORIGINS = frozenset({"owner", "goal_followup", "conversation", "capability_gap", "self"})
OUTCOMES = frozenset(
    {"completed", "no_op", "deferred", "rejected", "failed", "cancelled", "awaiting_approval"}
)
TRIGGERS = frozenset({"schedule", "event", "owner"})
LEASE_SECONDS = 300


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def title_key(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


@dataclass(frozen=True, slots=True)
class LifeSettings:
    """Owner controls. Persisted, validated, and re-read live by the runner."""

    paused: bool = False
    emergency_stop: bool = False
    max_cycles_per_day: int = 24
    min_interval_seconds: int = 300
    time_budget_seconds: int = 20
    stale_goal_days: int = 14
    max_attempts: int = 3
    max_cpu_percent: int = 85
    max_memory_percent: int = 85


_SETTING_RANGES: dict[str, tuple[int, int]] = {
    "max_cycles_per_day": (1, 500),
    "min_interval_seconds": (0, 86_400),
    "time_budget_seconds": (1, 600),
    "stale_goal_days": (1, 365),
    "max_attempts": (1, 10),
    "max_cpu_percent": (10, 100),
    "max_memory_percent": (10, 100),
}
_BOOL_SETTINGS = {"paused", "emergency_stop"}


def validate_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Return a cleaned copy or raise ValueError; unknown keys are rejected."""
    known = {f.name for f in fields(LifeSettings)}
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"unknown setting: {key}")
        if key in _BOOL_SETTINGS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            cleaned[key] = value
            continue
        low, high = _SETTING_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a whole number")
        if isinstance(value, float) and (not math.isfinite(value) or value != int(value)):
            raise ValueError(f"{key} must be a whole number")
        if not low <= int(value) <= high:
            raise ValueError(f"{key} must be between {low} and {high}")
        cleaned[key] = int(value)
    return cleaned


def _unit(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return float(value)


class LifeStore:
    MAX_TITLE = 300
    MAX_TEXT = 4000

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Distinguishes this process/instance's leases from a crashed earlier one.
        self.instance_id = f"{os.getpid()}:{uuid.uuid4().hex[:12]}"
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS life_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS curiosity_items (
                    item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    title_key TEXT NOT NULL,
                    why_it_matters TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    source_key TEXT UNIQUE,
                    source_ref_json TEXT NOT NULL,
                    expected_value REAL NOT NULL,
                    novelty REAL NOT NULL,
                    state TEXT NOT NULL,
                    state_reason TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    defer_until TEXT,
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_curiosity_state
                    ON curiosity_items(state, updated_at DESC);
                CREATE TABLE IF NOT EXISTS activity_journal (
                    cycle_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    trigger_detail TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    item_id TEXT,
                    rationale TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    outcome TEXT,
                    summary TEXT,
                    sources_json TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    permission_level INTEGER NOT NULL,
                    policy_json TEXT NOT NULL,
                    resource_json TEXT NOT NULL,
                    duration_ms INTEGER,
                    error TEXT,
                    next_follow_up TEXT,
                    notification_decision TEXT NOT NULL,
                    repeat_count INTEGER NOT NULL DEFAULT 1,
                    lease_owner TEXT,
                    lease_expires_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_journal_started
                    ON activity_journal(started_at DESC);
            """)

    # --------------------------------------------------------------- settings

    def get_settings(self) -> LifeSettings:
        with self._connection() as db:
            row = db.execute(
                "SELECT value_json FROM life_settings WHERE key = 'settings'"
            ).fetchone()
        stored = json.loads(row["value_json"]) if row else {}
        try:
            return LifeSettings(**validate_settings(stored))
        except ValueError:  # a corrupted row must fall back to safe defaults
            return LifeSettings(paused=True)

    def update_settings(self, changes: dict[str, Any]) -> LifeSettings:
        cleaned = validate_settings(changes)
        current = asdict(self.get_settings())
        current.update(cleaned)
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO life_settings (key, value_json, updated_at)
                VALUES ('settings', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json,
                                               updated_at = excluded.updated_at
                """,
                (json.dumps(current, sort_keys=True), _iso(utc_now())),
            )
        return LifeSettings(**current)

    # ---------------------------------------------------------------- backlog

    def add_item(
        self,
        *,
        title: str,
        why_it_matters: str,
        origin: str,
        source_ref: dict[str, Any] | None = None,
        source_key: str | None = None,
        expected_value: float = 0.5,
        novelty: float = 0.5,
    ) -> tuple[str, bool]:
        """Add an item. Returns (item_id, created); duplicates return the existing id."""
        if origin not in ORIGINS:
            raise ValueError("origin must be one of " + ", ".join(sorted(ORIGINS)))
        title = title.strip()
        why = why_it_matters.strip()
        if not title or not why:
            raise ValueError("title and why_it_matters are required")
        if len(title) > self.MAX_TITLE or len(why) > self.MAX_TEXT:
            raise ValueError("title or why_it_matters is too long")
        value = _unit(expected_value, "expected_value")
        novel = _unit(novelty, "novelty")
        key = title_key(title)
        ref_json = json.dumps(source_ref or {}, ensure_ascii=False, sort_keys=True)
        now = _iso(utc_now())
        with self._connection() as db:
            if source_key:
                row = db.execute(
                    "SELECT item_id FROM curiosity_items WHERE source_key = ?", (source_key,)
                ).fetchone()
                if row:
                    return str(row["item_id"]), False
            placeholders = ",".join("?" for _ in OPEN_STATES)
            row = db.execute(
                f"SELECT item_id FROM curiosity_items WHERE title_key = ? "  # noqa: S608
                f"AND state IN ({placeholders})",
                (key, *sorted(OPEN_STATES)),
            ).fetchone()
            if row:
                return str(row["item_id"]), False
            item_id = uuid.uuid4().hex
            db.execute(
                """
                INSERT INTO curiosity_items (
                    item_id, title, title_key, why_it_matters, origin, source_key,
                    source_ref_json, expected_value, novelty, state, attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
                """,
                (item_id, title, key, why, origin, source_key, ref_json, value, novel, now, now),
            )
        return item_id, True

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM curiosity_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        return self._decode_item(row) if row else None

    def list_items(self, state: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM curiosity_items", []
        if state:
            query += " WHERE state = ?"
            params.append(state)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._decode_item(row) for row in rows]

    def counts_by_state(self) -> dict[str, int]:
        with self._connection() as db:
            rows = db.execute(
                "SELECT state, COUNT(*) AS n FROM curiosity_items GROUP BY state"
            ).fetchall()
        return {str(row["state"]): int(row["n"]) for row in rows}

    def set_item_state(
        self,
        item_id: str,
        state: str,
        *,
        reason: str | None = None,
        defer_until: datetime | None = None,
        result: dict[str, Any] | None = None,
        count_attempt: bool = False,
        reset_attempts: bool = False,
    ) -> None:
        if state not in ITEM_STATES:
            raise ValueError("unknown item state")
        now = utc_now()
        with self._connection() as db:
            row = db.execute(
                "SELECT attempts FROM curiosity_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise KeyError(item_id)
            db.execute(
                """
                UPDATE curiosity_items
                SET state = ?, state_reason = ?, defer_until = ?, updated_at = ?,
                    attempts = ?, last_attempt_at = CASE WHEN ? THEN ? ELSE last_attempt_at END,
                    result_json = COALESCE(?, result_json)
                WHERE item_id = ?
                """,
                (
                    state,
                    reason,
                    _iso(defer_until) if defer_until else None,
                    _iso(now),
                    0 if reset_attempts else int(row["attempts"]) + (1 if count_attempt else 0),
                    1 if count_attempt else 0,
                    _iso(now),
                    json.dumps(result, ensure_ascii=False, sort_keys=True) if result else None,
                    item_id,
                ),
            )

    def delete_item(self, item_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute("DELETE FROM curiosity_items WHERE item_id = ?", (item_id,))
        return cursor.rowcount > 0

    def eligible_items(self, *, max_attempts: int, now: datetime | None = None) -> list[dict[str, Any]]:
        """Queued items, plus deferred ones whose wait is over, still under the attempt cap."""
        moment = _iso(now or utc_now())
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT * FROM curiosity_items
                WHERE attempts < ?
                  AND (state = 'queued'
                       OR (state = 'deferred' AND (defer_until IS NULL OR defer_until <= ?)))
                """,
                (max_attempts, moment),
            ).fetchall()
        return [self._decode_item(row) for row in rows]

    # ---------------------------------------------------------------- journal

    def start_cycle(
        self,
        *,
        trigger: str,
        trigger_detail: str,
        goal: str,
        rationale: str,
        item_id: str | None,
        policy: dict[str, Any],
        permission_level: int = 0,
    ) -> str:
        if trigger not in TRIGGERS:
            raise ValueError("trigger must be schedule, event or owner")
        cycle_id = uuid.uuid4().hex
        now = utc_now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO activity_journal (
                    cycle_id, status, trigger, trigger_detail, goal, item_id, rationale,
                    started_at, sources_json, actions_json, permission_level, policy_json,
                    resource_json, notification_decision, lease_owner, lease_expires_at
                ) VALUES (?, 'running', ?, ?, ?, ?, ?, ?, '[]', '[]', ?, ?, '{}', ?, ?, ?)
                """,
                (
                    cycle_id, trigger, trigger_detail[:500], goal[:500], item_id,
                    rationale[:2000], _iso(now), permission_level,
                    json.dumps(policy, sort_keys=True, default=str),
                    "none: proactive delivery is not implemented",
                    self.instance_id,
                    _iso(now + timedelta(seconds=LEASE_SECONDS)),
                ),
            )
        return cycle_id

    def finish_cycle(
        self,
        cycle_id: str,
        *,
        outcome: str,
        summary: str,
        sources: list[dict[str, Any]] | None = None,
        actions: list[str] | None = None,
        resource: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        next_follow_up: str | None = None,
    ) -> None:
        if outcome not in OUTCOMES:
            raise ValueError("unknown outcome")
        with self._connection() as db:
            cursor = db.execute(
                """
                UPDATE activity_journal
                SET status = 'finished', ended_at = ?, outcome = ?, summary = ?,
                    sources_json = ?, actions_json = ?, resource_json = ?, duration_ms = ?,
                    error = ?, next_follow_up = ?, lease_owner = NULL, lease_expires_at = NULL
                WHERE cycle_id = ? AND status = 'running'
                """,
                (
                    _iso(utc_now()), outcome, summary[:2000],
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(actions or [], ensure_ascii=False),
                    json.dumps(resource or {}, sort_keys=True, default=str),
                    duration_ms, error[:500] if error else None, next_follow_up,
                    cycle_id,
                ),
            )
        if cursor.rowcount == 0:
            raise KeyError(cycle_id)

    def record_no_op(
        self,
        *,
        trigger: str,
        trigger_detail: str,
        reason: str,
        policy: dict[str, Any],
        coalesce_seconds: int = 6 * 3600,
    ) -> str:
        """Journal a real cycle that found nothing to do, coalescing identical repeats."""
        now = utc_now()
        with self._connection() as db:
            last = db.execute(
                "SELECT cycle_id, outcome, summary, ended_at, repeat_count "
                "FROM activity_journal ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            if (
                last
                and last["outcome"] == "no_op"
                and last["summary"] == reason
                and last["ended_at"]
                and datetime.fromisoformat(last["ended_at"]) > now - timedelta(seconds=coalesce_seconds)
            ):
                db.execute(
                    "UPDATE activity_journal SET repeat_count = repeat_count + 1, ended_at = ? "
                    "WHERE cycle_id = ?",
                    (_iso(now), last["cycle_id"]),
                )
                return str(last["cycle_id"])
        cycle_id = self.start_cycle(
            trigger=trigger,
            trigger_detail=trigger_detail,
            goal="Look for useful work",
            rationale="No eligible curiosity item",
            item_id=None,
            policy=policy,
        )
        self.finish_cycle(cycle_id, outcome="no_op", summary=reason, duration_ms=0)
        return cycle_id

    def get_cycle(self, cycle_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM activity_journal WHERE cycle_id = ?", (cycle_id,)
            ).fetchone()
        return self._decode_cycle(row) if row else None

    def list_journal(self, limit: int = 50, outcome: str | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT * FROM activity_journal", []
        if outcome:
            query += " WHERE outcome = ?"
            params.append(outcome)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connection() as db:
            rows = db.execute(query, params).fetchall()
        return [self._decode_cycle(row) for row in rows]

    def cycles_started_since(self, since: datetime) -> int:
        """Real (non-coalesced) cycles started since `since`, no-ops included."""
        with self._connection() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(repeat_count), 0) AS n FROM activity_journal "
                "WHERE started_at >= ?",
                (_iso(since),),
            ).fetchone()
        return int(row["n"])

    def last_cycle_started_at(self) -> datetime | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT started_at, ended_at, status FROM activity_journal "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        moment = row["ended_at"] if row["status"] == "finished" and row["ended_at"] else row["started_at"]
        return datetime.fromisoformat(moment)

    def recover_interrupted(self, *, at_startup: bool = False) -> list[str]:
        """Fail running cycles left by an earlier process; requeue their item.

        Never replays work: the item returns to the queue with the attempt counted,
        and the journal says the cycle was interrupted. At startup (Mia runs as a
        single local server process) every lease not owned by this instance is dead;
        otherwise a lease another instance still holds unexpired is left alone.
        """
        recovered: list[str] = []
        now = utc_now()
        with self._connection() as db:
            rows = db.execute(
                "SELECT cycle_id, item_id, lease_owner, lease_expires_at "
                "FROM activity_journal WHERE status = 'running'"
            ).fetchall()
        for row in rows:
            held_elsewhere = not at_startup and (
                row["lease_owner"] not in (None, self.instance_id)
                and row["lease_expires_at"]
                and datetime.fromisoformat(row["lease_expires_at"]) > now
            )
            ours = row["lease_owner"] == self.instance_id
            if held_elsewhere or ours:
                continue
            self.finish_cycle(
                row["cycle_id"],
                outcome="failed",
                summary="Cycle was interrupted by a restart or crash; nothing was resumed",
                error="interrupted",
            )
            if row["item_id"]:
                try:
                    self.set_item_state(
                        row["item_id"], "queued",
                        reason="interrupted cycle; requeued", count_attempt=True,
                    )
                except KeyError:
                    pass
            recovered.append(str(row["cycle_id"]))
        return recovered

    # --------------------------------------------------------------- internal

    @staticmethod
    def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["source_ref"] = json.loads(result.pop("source_ref_json"))
        raw = result.pop("result_json")
        result["result"] = json.loads(raw) if raw else None
        result.pop("title_key", None)
        return result

    @staticmethod
    def _decode_cycle(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        for column in ("sources", "actions", "policy", "resource"):
            result[column] = json.loads(result.pop(f"{column}_json"))
        return result
