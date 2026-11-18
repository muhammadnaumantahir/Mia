"""Durable task lifecycle and a resource-aware, opt-in dispatcher."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


DEFAULT_LEASE_SECONDS = 300


def _lease_expiry(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


TASK_SOURCES = frozenset({
    "interactive",
    "scheduled",
    "self_assessment",
    "internal_test",
    "benchmark",
    "research",
    "environment",
    "experience",
})

TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "rejected", "cancelled"}),
    "approved": frozenset({"running", "cancelled"}),
    "running": frozenset({"waiting", "completed", "failed", "cancelled"}),
    "waiting": frozenset({"running", "failed", "cancelled"}),
    "failed": frozenset({"approved", "cancelled"}),
    "completed": frozenset(),
    "rejected": frozenset(),
    "cancelled": frozenset(),
}


class TaskTransitionError(RuntimeError):
    """A task transition is invalid or conflicts with the stored state."""


class TaskStore:
    """SQLite-backed task ledger with append-only state transition history."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # "<pid>:<random>" identifies this store instance as a lease holder.
        self.lease_owner = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.lease_seconds = DEFAULT_LEASE_SECONDS
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
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approved_at TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    metadata_json TEXT NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS task_discovery_keys (
                    dedupe_key TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_events (
                    event_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    occurred_at TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_queue
                ON tasks(status, priority DESC, created_at ASC);

                CREATE INDEX IF NOT EXISTS idx_task_events_time
                ON task_events(task_id, occurred_at ASC);

                CREATE TABLE IF NOT EXISTS runtime_controls (
                    control_key TEXT PRIMARY KEY,
                    control_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            self._migrate_leases(db)

    def _migrate_leases(self, db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)").fetchall()}
        if "lease_owner" not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN lease_owner TEXT")
        if "lease_expires_at" not in columns:
            db.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at TEXT")

    def is_autonomy_paused(self) -> bool:
        """Return the owner pause flag, which persists across Mia restarts."""
        with self._connection() as db:
            row = db.execute(
                "SELECT control_value FROM runtime_controls WHERE control_key = ?",
                ("autonomy_paused",),
            ).fetchone()
        if row is None:
            return False
        value = str(row["control_value"])
        if value not in {"true", "false"}:
            raise TaskTransitionError("stored autonomy pause setting is invalid")
        return value == "true"

    def set_autonomy_paused(self, paused: bool) -> None:
        """Persist the background dispatcher pause state."""
        if not isinstance(paused, bool):
            raise ValueError("paused must be a boolean")
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO runtime_controls (control_key, control_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(control_key) DO UPDATE SET
                    control_value = excluded.control_value,
                    updated_at = excluded.updated_at
                """,
                ("autonomy_paused", "true" if paused else "false", _now()),
            )

    def create_task(
        self,
        *,
        title: str,
        goal: str,
        source: str = "interactive",
        priority: int = 50,
        metadata: dict[str, Any] | None = None,
        actor: str = "user",
        dedupe_key: str | None = None,
    ) -> str:
        if not title.strip() or len(title) > 160:
            raise ValueError("title must be 1-160 characters")
        if not goal.strip() or len(goal) > 20_000:
            raise ValueError("goal must be 1-20,000 characters")
        if source not in TASK_SOURCES:
            raise ValueError("unsupported task source")
        if not 0 <= priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if dedupe_key is not None and not re.fullmatch(r"[a-f0-9]{64}", dedupe_key):
            raise ValueError("dedupe_key must be a lowercase SHA-256 digest")

        task_id = uuid.uuid4().hex
        now = _now()
        payload = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if dedupe_key:
                existing = db.execute(
                    "SELECT task_id FROM task_discovery_keys WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if existing is not None:
                    return str(existing["task_id"])
            db.execute(
                """
                INSERT INTO tasks (
                    task_id, title, goal, source, status, priority,
                    created_at, updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (task_id, title.strip(), goal.strip(), source, priority, now, now, payload),
            )
            if dedupe_key:
                db.execute(
                    """
                    INSERT INTO task_discovery_keys (dedupe_key, task_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (dedupe_key, task_id, now),
                )
            self._insert_event(
                db,
                task_id=task_id,
                from_status=None,
                to_status="proposed",
                reason="Task created and awaiting approval",
                actor=actor,
                payload={},
            )
        return task_id

    def transition(
        self,
        task_id: str,
        new_status: str,
        *,
        reason: str,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if new_status not in TASK_TRANSITIONS:
            raise ValueError("unknown task status")
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("a 1-1000 character transition reason is required")

        now = _now()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskTransitionError("task was not found")
            old_status = row["status"]
            if new_status not in TASK_TRANSITIONS[old_status]:
                raise TaskTransitionError(
                    f"transition {old_status} -> {new_status} is not allowed"
                )

            approved_at = now if new_status == "approved" else row["approved_at"]
            started_at = now if new_status == "running" and row["started_at"] is None else row["started_at"]
            completed_at = (
                now if new_status in {"completed", "failed", "cancelled", "rejected"}
                else row["completed_at"]
            )
            last_error = (
                str((payload or {}).get("error_type", "task_failed"))
                if new_status == "failed"
                else None if new_status in {"approved", "running"}
                else row["last_error"]
            )

            db.execute(
                """
                UPDATE tasks
                SET status = ?, updated_at = ?, approved_at = ?,
                    started_at = ?, completed_at = ?, last_error = ?,
                    lease_owner = ?, lease_expires_at = ?
                WHERE task_id = ? AND status = ?
                """,
                (
                    new_status,
                    now,
                    approved_at,
                    started_at,
                    completed_at,
                    last_error,
                    self.lease_owner if new_status == "running" else None,
                    _lease_expiry(self.lease_seconds) if new_status == "running" else None,
                    task_id,
                    old_status,
                ),
            )
            self._insert_event(
                db,
                task_id=task_id,
                from_status=old_status,
                to_status=new_status,
                reason=reason,
                actor=actor,
                payload=payload or {},
            )
            updated = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._decode_task(updated)

    def renew_lease(self, task_id: str) -> bool:
        """Extend this store's lease on a running task; False if it no longer holds it."""
        with self._connection() as db:
            cursor = db.execute(
                """
                UPDATE tasks SET lease_expires_at = ?
                WHERE task_id = ? AND status = 'running' AND lease_owner = ?
                """,
                (_lease_expiry(self.lease_seconds), task_id, self.lease_owner),
            )
        return cursor.rowcount == 1

    def recover_interrupted_tasks(self) -> list[str]:
        """Mark running tasks without a live lease as failed (startup recovery).

        A task is left alone only when another Mia process (different pid)
        still holds an unexpired lease. Legacy rows without a lease, expired
        leases and leases from an earlier store instance in this process are
        recovered. Recovery is never an automatic retry: the owner must review
        and explicitly re-approve a failed task.
        """
        return self._recover_running(startup=True)

    def recover_expired_leases(self) -> list[str]:
        """Fail running tasks whose lease expired and is not held by this store.

        Cheap enough to call every dispatch cycle; it catches a worker that
        crashed while another process stayed up.
        """
        return self._recover_running(startup=False)

    def _recover_running(self, *, startup: bool) -> list[str]:
        now = _now()
        recovered: list[str] = []
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT task_id, lease_owner, lease_expires_at FROM tasks
                WHERE status = 'running'
                ORDER BY started_at, created_at
                """
            ).fetchall()
            for row in rows:
                task_id = str(row["task_id"])
                owner = row["lease_owner"]
                expires = row["lease_expires_at"]
                live = expires is not None and expires > now
                if owner == self.lease_owner:
                    if not startup:
                        continue  # never reap our own running work mid-session
                elif live and not str(owner).startswith(f"{os.getpid()}:"):
                    continue  # another live process holds the lease
                elif not startup and expires is None:
                    continue  # legacy row: only startup recovery may judge it
                elif not startup and live:
                    continue
                updated = db.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed', updated_at = ?, completed_at = ?,
                        last_error = ?, lease_owner = NULL, lease_expires_at = NULL
                    WHERE task_id = ? AND status = 'running'
                    """,
                    (
                        now,
                        now,
                        "interrupted_on_restart" if startup else "lease_expired",
                        task_id,
                    ),
                )
                if updated.rowcount != 1:
                    continue
                self._insert_event(
                    db,
                    task_id=task_id,
                    from_status="running",
                    to_status="failed",
                    reason=(
                        "The previous Mia process stopped before recording a task result; "
                        "owner review is required before retry"
                        if startup
                        else "The worker lease expired without a recorded result; "
                        "owner review is required before retry"
                    ),
                    actor="startup-recovery" if startup else "lease-recovery",
                    payload={
                        "recovery": "interrupted_on_restart" if startup else "lease_expired",
                        "previous_lease_owner": owner,
                        "previous_lease_expires_at": expires,
                        "retry_requires_owner_approval": True,
                    },
                )
                recovered.append(task_id)
        return recovered

    def claim_next(
        self,
        *,
        actor: str = "autonomy-runtime",
        validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim the highest-priority approved task accepted by validator.

        A rejected candidate remains approved and queued; it is never marked
        failed merely because it is outside the automatic executor's scope.
        """
        now = _now()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            candidates = db.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'approved'
                ORDER BY priority DESC, created_at ASC
                """
            ).fetchall()
            row = None
            for candidate in candidates:
                decoded = self._decode_task(candidate)
                if validator is None or validator(decoded):
                    row = candidate
                    break
            if row is None:
                return None
            updated = db.execute(
                """
                UPDATE tasks
                SET status = 'running', updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    lease_owner = ?, lease_expires_at = ?
                WHERE task_id = ? AND status = 'approved'
                """,
                (
                    now,
                    now,
                    self.lease_owner,
                    _lease_expiry(self.lease_seconds),
                    row["task_id"],
                ),
            )
            if updated.rowcount != 1:
                return None
            self._insert_event(
                db,
                task_id=row["task_id"],
                from_status="approved",
                to_status="running",
                reason="Claimed by the autonomy dispatcher",
                actor=actor,
                payload={},
            )
            claimed = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (row["task_id"],),
            ).fetchone()
        return self._decode_task(claimed)

    def claim_task(
        self,
        task_id: str,
        *,
        actor: str = "manual-task-executor",
    ) -> dict[str, Any]:
        """Atomically claim one specific task, but only after owner approval."""
        now = _now()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskTransitionError("task was not found")
            if row["status"] != "approved":
                raise TaskTransitionError("task must be approved before it can run")
            updated = db.execute(
                """
                UPDATE tasks
                SET status = 'running', updated_at = ?,
                    started_at = COALESCE(started_at, ?),
                    lease_owner = ?, lease_expires_at = ?
                WHERE task_id = ? AND status = 'approved'
                """,
                (now, now, self.lease_owner, _lease_expiry(self.lease_seconds), task_id),
            )
            if updated.rowcount != 1:
                raise TaskTransitionError("task is no longer available to execute")
            self._insert_event(
                db,
                task_id=task_id,
                from_status="approved",
                to_status="running",
                reason="Claimed for explicit manual execution",
                actor=actor,
                payload={},
            )
            claimed = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._decode_task(claimed)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._decode_task(row) if row else None

    def list_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT * FROM tasks
                ORDER BY
                    CASE status
                        WHEN 'running' THEN 0
                        WHEN 'waiting' THEN 1
                        WHEN 'proposed' THEN 2
                        WHEN 'approved' THEN 3
                        ELSE 4
                    END,
                    priority DESC,
                    created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._decode_task(row) for row in rows]

    def list_events(self, task_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT * FROM task_events
                WHERE task_id = ?
                ORDER BY occurred_at ASC
                LIMIT ?
                """,
                (task_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _insert_event(
        db: sqlite3.Connection,
        *,
        task_id: str,
        from_status: str | None,
        to_status: str,
        reason: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        db.execute(
            """
            INSERT INTO task_events (
                event_id, task_id, occurred_at, from_status, to_status,
                reason, actor, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                task_id,
                _now(),
                from_status,
                to_status,
                reason,
                actor,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    @staticmethod
    def _decode_task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result


@dataclass(frozen=True, slots=True)
class RuntimeLoad:
    cpu_percent: float
    memory_percent: float

    def __post_init__(self) -> None:
        for name, value in (
            ("cpu_percent", self.cpu_percent),
            ("memory_percent", self.memory_percent),
        ):
            if not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError(f"{name} must be a finite percentage between 0 and 100")


@dataclass(frozen=True, slots=True)
class AutonomyPolicy:
    enabled: bool = False
    emergency_stop: bool = False
    max_cpu_percent: float = 75.0
    max_memory_percent: float = 85.0
    max_tasks_per_cycle: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("max_cpu_percent", self.max_cpu_percent),
            ("max_memory_percent", self.max_memory_percent),
        ):
            if not math.isfinite(value) or not 1 <= value <= 100:
                raise ValueError(f"{name} must be a finite percentage between 1 and 100")
        if not 1 <= self.max_tasks_per_cycle <= 10:
            raise ValueError("max_tasks_per_cycle must be between 1 and 10")


class TaskCoordinator:
    """Single-cycle dispatcher. No background thread or implicit scheduler is started."""

    def __init__(self, store: TaskStore, policy: AutonomyPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or AutonomyPolicy()

    def _transition_after_execution(
        self,
        task_id: str,
        new_status: str,
        *,
        reason: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Do not overwrite a state changed by an owner during execution."""
        try:
            return self.store.transition(
                task_id,
                new_status,
                reason=reason,
                actor="task-coordinator",
                payload=payload,
            )
        except TaskTransitionError:
            current = self.store.get_task(task_id)
            if current is None or current["status"] == "running":
                raise
            return current

    def dispatch_one(
        self,
        *,
        load: RuntimeLoad | None,
        executor: Callable[[dict[str, Any]], dict[str, Any] | None],
        validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        policy = self.policy
        if not policy.enabled:
            return {"status": "paused", "reason": "autonomy_disabled"}
        if policy.emergency_stop:
            return {"status": "paused", "reason": "emergency_stop"}
        if load is None:
            return {"status": "paused", "reason": "runtime_load_unavailable"}
        if (
            load.cpu_percent >= policy.max_cpu_percent
            or load.memory_percent >= policy.max_memory_percent
        ):
            return {"status": "paused", "reason": "resource_limit"}

        self.store.recover_expired_leases()
        task = self.store.claim_next(validator=validator)
        if task is None:
            return {"status": "idle", "reason": "no_approved_tasks"}

        try:
            outcome = executor(task) or {}
            if not isinstance(outcome, dict):
                raise TypeError("task executor must return a result object")
            if outcome.get("status") == "waiting":
                waiting = self._transition_after_execution(
                    task["task_id"],
                    "waiting",
                    reason=str(outcome.get("reason") or "Task is waiting for an external approval"),
                    payload={
                        "outcome": outcome,
                        "approval_required": True,
                    },
                )
                if waiting["status"] != "waiting":
                    return {
                        "status": waiting["status"],
                        "task": waiting,
                        "outcome": outcome,
                        "reason": "task_state_changed_during_execution",
                    }
                return {
                    "status": "waiting",
                    "task": waiting,
                    "outcome": outcome,
                }
            finished = self._transition_after_execution(
                task["task_id"],
                "completed",
                reason="Task executor returned successfully",
                payload={"outcome": outcome},
            )
            if finished["status"] != "completed":
                return {
                    "status": finished["status"],
                    "task": finished,
                    "outcome": outcome,
                    "reason": "task_state_changed_during_execution",
                }
            return {
                "status": "completed",
                "task": finished,
                "outcome": outcome,
            }
        except Exception as exc:
            failed = self._transition_after_execution(
                task["task_id"],
                "failed",
                reason="Task executor raised an exception",
                payload={"error_type": type(exc).__name__},
            )
            if failed["status"] != "failed":
                return {
                    "status": failed["status"],
                    "task": failed,
                    "error_type": type(exc).__name__,
                    "reason": "task_state_changed_during_execution",
                }
            return {
                "status": "failed",
                "task": failed,
                "error_type": type(exc).__name__,
            }
