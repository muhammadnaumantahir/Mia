"""Plans, step state, checkpoints and the plan runner (SPS-03 planning, SPS-10 lifecycle).

Every step has an explicit status: planned, attempted, completed, failed, verified
(plus blocked/skipped/awaiting_*). "completed" means the executor reported success;
"verified" means an independent check confirmed the result. Plans are persisted
independently of any model/provider, so switching or losing a provider never loses
task state.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

MAX_STEPS = 8
STEP_STATUSES = {
    "planned", "attempted", "completed", "verified", "failed",
    "blocked", "skipped", "awaiting_approval", "awaiting_input",
}
DONE_STATUSES = {"completed", "verified"}
TERMINAL_FAILURE = {"failed", "blocked", "skipped"}
ERROR_CLASSES = {"transient", "permanent", "permission", "validation", "needs_approval", "needs_input"}


class PlanError(ValueError):
    """The plan is structurally invalid."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class PlanStep:
    id: str
    title: str
    kind: str = "reason"  # "reason" | "tool" | "ask"
    description: str = ""
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    status: str = "planned"
    attempts: int = 0
    result_summary: str = ""
    error: str = ""
    error_class: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "kind": self.kind, "description": self.description,
            "tool_name": self.tool_name, "arguments": self.arguments, "depends_on": self.depends_on,
            "status": self.status, "attempts": self.attempts, "result_summary": self.result_summary,
            "error": self.error, "error_class": self.error_class, "evidence": self.evidence,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


@dataclass(slots=True)
class Plan:
    id: str
    objective: str
    steps: list[PlanStep]
    conversation_id: str | None = None
    status: str = "planned"
    revision: int = 1
    constraints: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ---- structure ----
    def validate(self) -> None:
        if not self.objective.strip():
            raise PlanError("objective is required")
        if not 1 <= len(self.steps) <= MAX_STEPS:
            raise PlanError(f"a plan needs 1-{MAX_STEPS} steps")
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise PlanError("step ids must be unique")
        by_id = {s.id: s for s in self.steps}
        for step in self.steps:
            if step.status not in STEP_STATUSES:
                raise PlanError(f"invalid status: {step.status}")
            if step.kind not in {"reason", "tool", "ask"}:
                raise PlanError(f"invalid step kind: {step.kind}")
            if step.kind == "tool" and not step.tool_name:
                raise PlanError(f"tool step {step.id} has no tool_name")
            for dep in step.depends_on:
                if dep not in by_id:
                    raise PlanError(f"step {step.id} depends on unknown step {dep}")
                if dep == step.id:
                    raise PlanError(f"step {step.id} depends on itself")
        # cycle check (DFS)
        state: dict[str, int] = {}

        def visit(node: str) -> None:
            if state.get(node) == 2:
                return
            if state.get(node) == 1:
                raise PlanError("plan has a dependency cycle")
            state[node] = 1
            for dep in by_id[node].depends_on:
                visit(dep)
            state[node] = 2

        for sid in ids:
            visit(sid)

    def step(self, step_id: str) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def ready_steps(self) -> list[PlanStep]:
        done = {s.id for s in self.steps if s.status in DONE_STATUSES}
        return [
            s for s in self.steps
            if s.status == "planned" and all(d in done for d in s.depends_on)
        ]

    def propagate_blocks(self) -> list[str]:
        """Mark steps whose dependencies failed/blocked as blocked; return their ids."""
        blocked: list[str] = []
        changed = True
        while changed:
            changed = False
            bad = {s.id for s in self.steps if s.status in TERMINAL_FAILURE}
            for s in self.steps:
                if s.status == "planned" and any(d in bad for d in s.depends_on):
                    s.status = "blocked"
                    s.error = "a prerequisite step did not complete"
                    s.updated_at = _now()
                    blocked.append(s.id)
                    changed = True
        return blocked

    def recompute_status(self) -> str:
        statuses = [s.status for s in self.steps]
        if any(s == "awaiting_approval" for s in statuses):
            self.status = "awaiting_approval"
        elif any(s == "awaiting_input" for s in statuses):
            self.status = "awaiting_input"
        elif all(s in DONE_STATUSES for s in statuses):
            self.status = "completed"
        elif any(s == "planned" for s in statuses):
            self.status = "running" if any(s in DONE_STATUSES for s in statuses) else "planned"
        elif any(s in DONE_STATUSES for s in statuses):
            self.status = "partial"
        else:
            self.status = "failed"
        self.updated_at = _now()
        return self.status

    def progress(self) -> dict[str, Any]:
        total = len(self.steps)
        counts: dict[str, int] = {}
        for s in self.steps:
            counts[s.status] = counts.get(s.status, 0) + 1
        done = sum(counts.get(k, 0) for k in DONE_STATUSES)
        return {"total": total, "done": done, "by_status": counts,
                "fraction": round(done / total, 3) if total else 0.0}

    def revise(self, new_steps: list[PlanStep]) -> None:
        """Replace unfinished steps, keeping completed/verified work untouched."""
        kept = [s for s in self.steps if s.status in DONE_STATUSES]
        kept_ids = {s.id for s in kept}
        clash = kept_ids & {s.id for s in new_steps}
        if clash:
            raise PlanError(f"revised steps reuse completed step ids: {sorted(clash)}")
        candidate = Plan(self.id, self.objective, [*kept, *new_steps], self.conversation_id)
        candidate.validate()
        self.steps = candidate.steps
        self.revision += 1
        self.recompute_status()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "objective": self.objective, "conversation_id": self.conversation_id,
            "status": self.status, "revision": self.revision, "constraints": self.constraints,
            "steps": [s.to_dict() for s in self.steps], "progress": self.progress(),
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            id=data["id"], objective=data["objective"],
            steps=[PlanStep.from_dict(s) for s in data["steps"]],
            conversation_id=data.get("conversation_id"), status=data.get("status", "planned"),
            revision=data.get("revision", 1), constraints=list(data.get("constraints", [])),
            created_at=data.get("created_at", _now()), updated_at=data.get("updated_at", _now()),
        )


def new_plan(objective: str, steps: list[PlanStep], conversation_id: str | None = None,
             constraints: list[str] | None = None) -> Plan:
    plan = Plan(uuid.uuid4().hex[:16], objective.strip(), steps, conversation_id,
                constraints=list(constraints or []))
    plan.validate()
    return plan


class PlanStore:
    """SQLite persistence for plans and checkpoints, independent of any provider."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS brain_plans(
                  plan_id TEXT PRIMARY KEY, conversation_id TEXT, status TEXT NOT NULL,
                  body TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_brain_plans_conv
                  ON brain_plans(conversation_id, updated_at);
                CREATE TABLE IF NOT EXISTS brain_checkpoints(
                  checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                  label TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_brain_ckpt_plan ON brain_checkpoints(plan_id);
                """
            )

    @contextmanager
    def _conn(self):
        with self._lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            finally:
                db.close()

    def save(self, plan: Plan) -> None:
        body = json.dumps(plan.to_dict(), ensure_ascii=False)
        with self._conn() as db:
            db.execute(
                "INSERT INTO brain_plans(plan_id, conversation_id, status, body, updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET status=excluded.status, "
                "body=excluded.body, updated_at=excluded.updated_at",
                (plan.id, plan.conversation_id, plan.status, body, plan.updated_at),
            )

    def load(self, plan_id: str) -> Plan | None:
        with self._conn() as db:
            row = db.execute("SELECT body FROM brain_plans WHERE plan_id=?", (plan_id,)).fetchone()
        return Plan.from_dict(json.loads(row["body"])) if row else None

    def latest_for_conversation(self, conversation_id: str,
                                statuses: tuple[str, ...] | None = None) -> Plan | None:
        with self._conn() as db:
            rows = db.execute(
                "SELECT body FROM brain_plans WHERE conversation_id=? ORDER BY updated_at DESC, rowid DESC",
                (conversation_id,)).fetchall()
        for row in rows:
            plan = Plan.from_dict(json.loads(row["body"]))
            if statuses is None or plan.status in statuses:
                return plan
        return None

    def list_plans(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT body FROM brain_plans ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (max(1, min(limit, 100)),)).fetchall()
        return [Plan.from_dict(json.loads(r["body"])).to_dict() for r in rows]

    def checkpoint(self, plan: Plan, label: str) -> int:
        with self._conn() as db:
            cur = db.execute(
                "INSERT INTO brain_checkpoints(plan_id,label,body,created_at) VALUES(?,?,?,?)",
                (plan.id, label[:120], json.dumps(plan.to_dict(), ensure_ascii=False), _now()))
            return int(cur.lastrowid)

    def checkpoints(self, plan_id: str) -> list[dict[str, Any]]:
        with self._conn() as db:
            rows = db.execute(
                "SELECT checkpoint_id, label, created_at FROM brain_checkpoints "
                "WHERE plan_id=? ORDER BY checkpoint_id", (plan_id,)).fetchall()
        return [dict(r) for r in rows]

    def restore(self, plan_id: str, checkpoint_id: int) -> Plan | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT body FROM brain_checkpoints WHERE plan_id=? AND checkpoint_id=?",
                (plan_id, checkpoint_id)).fetchone()
        return Plan.from_dict(json.loads(row["body"])) if row else None

    def recover_interrupted(self) -> list[str]:
        """After a crash/restart: steps left 'attempted' return to 'planned' (retry-safe tools only)."""
        recovered: list[str] = []
        for data in self.list_plans(limit=100):
            plan = Plan.from_dict(data)
            touched = False
            for s in plan.steps:
                if s.status == "attempted":
                    s.status = "planned"
                    s.error = "interrupted before completion; will be retried"
                    touched = True
            if touched:
                plan.recompute_status()
                self.save(plan)
                recovered.append(plan.id)
        return recovered


@dataclass(frozen=True, slots=True)
class StepOutcome:
    ok: bool
    summary: str = ""
    error: str = ""
    error_class: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None


class StepExecutor(Protocol):
    async def execute(self, step: PlanStep, plan: Plan) -> StepOutcome: ...


Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RunPolicy:
    max_attempts: int = 2
    step_timeout_s: float = 60.0
    backoff_s: float = 0.5
    max_total_steps: int = 16  # hard cap on executed attempts per run


class PlanRunner:
    """Executes ready steps in dependency order with timeouts, retries and checkpoints."""

    def __init__(self, store: PlanStore | None, executor: StepExecutor,
                 policy: RunPolicy = RunPolicy(), sleep: Sleep = asyncio.sleep) -> None:
        self.store, self.executor, self.policy, self._sleep = store, executor, policy, sleep
        self.events: list[dict[str, Any]] = []

    def _save(self, plan: Plan, label: str | None = None) -> None:
        plan.recompute_status()
        if self.store is not None:
            self.store.save(plan)
            if label:
                self.store.checkpoint(plan, label)

    def _event(self, plan: Plan, step: PlanStep, kind: str, **extra: Any) -> None:
        self.events.append({"plan_id": plan.id, "step_id": step.id, "event": kind,
                            "status": step.status, **extra})

    async def _attempt(self, plan: Plan, step: PlanStep) -> StepOutcome:
        step.status = "attempted"
        step.attempts += 1
        step.updated_at = _now()
        self._save(plan)
        try:
            return await asyncio.wait_for(self.executor.execute(step, plan),
                                          timeout=self.policy.step_timeout_s)
        except TimeoutError:
            return StepOutcome(False, error="step timed out", error_class="transient")
        except Exception as exc:  # executor bugs must never kill the run
            return StepOutcome(False, error=f"{type(exc).__name__}: {str(exc)[:200]}",
                               error_class="permanent")

    async def run(self, plan: Plan) -> Plan:
        plan.validate()
        executed = 0
        while executed < self.policy.max_total_steps:
            plan.propagate_blocks()
            ready = plan.ready_steps()
            if not ready:
                break
            step = ready[0]
            outcome = await self._attempt(plan, step)
            executed += 1
            while (not outcome.ok and outcome.error_class == "transient"
                   and step.attempts < self.policy.max_attempts):
                self._event(plan, step, "retry", error=outcome.error)
                await self._sleep(self.policy.backoff_s * step.attempts)
                outcome = await self._attempt(plan, step)
                executed += 1
            step.updated_at = _now()
            step.evidence = dict(outcome.evidence)
            if outcome.ok:
                step.status, step.result_summary, step.error, step.error_class = (
                    "completed", outcome.summary[:2000], "", "")
                self._event(plan, step, "completed")
            elif outcome.error_class == "needs_approval":
                step.status, step.error_class = "awaiting_approval", "needs_approval"
                step.result_summary = outcome.summary[:500]
                step.evidence = {**step.evidence, "pending_action": outcome.pending_action}
                self._event(plan, step, "awaiting_approval")
                self._save(plan, f"awaiting approval: {step.id}")
                return plan
            elif outcome.error_class == "needs_input":
                step.status, step.error, step.error_class = (
                    "awaiting_input", outcome.error[:500], "needs_input")
                self._event(plan, step, "awaiting_input")
                self._save(plan, f"awaiting input: {step.id}")
                return plan
            else:
                step.status, step.error = "failed", outcome.error[:500]
                step.error_class = outcome.error_class if outcome.error_class in ERROR_CLASSES else "permanent"
                self._event(plan, step, "failed", error_class=step.error_class)
            self._save(plan, f"after step {step.id}: {step.status}")
        plan.propagate_blocks()
        self._save(plan, "run finished")
        return plan


def summarize_plan(plan: Plan) -> str:
    """Owner-facing progress summary: statuses and outcomes, never hidden reasoning."""
    lines = [f"Plan: {plan.objective} ({plan.status}, {plan.progress()['done']}/{len(plan.steps)} done)"]
    for s in plan.steps:
        tail = f" - {s.result_summary}" if s.result_summary else (f" - {s.error}" if s.error else "")
        lines.append(f"{s.id}. [{s.status}] {s.title}{tail}")
    return "\n".join(lines)
