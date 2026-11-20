"""Persistent multi-step computer task engine.

A task is an ordered plan of steps, each naming a registered computer capability. The engine:

* never lets a plan grant permission: every step is re-inspected and goes through its own
  approval (or an active owner grant) at execution time;
* checkpoints every step transition to SQLite, so after a restart it can say what finished,
  what was in flight and what remains;
* never silently resumes: interrupted tasks need an explicit owner resume, a step that was
  mid-flight when the process died is *unknown* until the owner retries it, and every pending
  step is re-validated against the current state (a stale approval simply stops matching);
* retries only idempotent read-only steps, within a small limit;
* reports only what it recorded: per-step verified/unverified/failed, never invented results.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .approvals import ApprovalError, ApprovalStore
from .control import CURRENT_TASK_ID, AutomationControl, AutomationStopped
from .permissions import Decision

MAX_STEPS = 20
TERMINAL_TASK_STATES = {"completed", "failed", "cancelled"}
_TEMPLATE = re.compile(r"\$\{steps\.(\d+)\.([A-Za-z0-9_.]+)\}")


class TaskPlanError(ValueError):
    """The submitted plan is invalid."""


@dataclass(slots=True)
class Inspection:
    """Result of validating a step against the current state (no side effects)."""

    summary: str
    risk: str = "low"
    needs_approval: bool = False


@dataclass(slots=True)
class Outcome:
    ok: bool
    verified: bool | None = None
    data: dict[str, Any] = field(default_factory=dict)
    note: str | None = None
    error: str | None = None
    retryable: bool = False


@dataclass(slots=True)
class StepHandler:
    capability: str
    inspect: Callable[[dict[str, Any]], Inspection]
    run: Callable[[dict[str, Any], str | None], Outcome]
    request_approval: Callable[[dict[str, Any]], str] | None = None
    idempotent: bool = False
    required: tuple[str, ...] = ()
    is_async: bool = False


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _lookup(data: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            raise KeyError(path)
    return data


class TaskEngine:
    def __init__(
        self,
        database_path: str | Path,
        approvals: ApprovalStore,
        handlers: dict[str, StepHandler],
        *,
        control: AutomationControl | None = None,
        permission_policy: Any | None = None,
        capability_registry: Any | None = None,
        permission_path: Callable[[str, dict[str, Any]], str | None] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.approvals = approvals
        self.handlers = handlers
        self.control = control
        self.permission_policy = permission_policy
        self.capability_registry = capability_registry
        self.permission_path = permission_path
        self._init()
        self._running: set[str] = set()
        self._cancel_callbacks: list[Callable[[str], Any]] = []
        self._background: dict[str, asyncio.Task] = {}

    # ---- storage --------------------------------------------------------------------------
    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS computer_tasks (
                task_id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT NOT NULL,
                status TEXT NOT NULL, outcome TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
                pause_requested INTEGER NOT NULL DEFAULT 0, final_report TEXT);
            CREATE TABLE IF NOT EXISTS computer_task_steps (
                task_id TEXT NOT NULL, idx INTEGER NOT NULL, capability TEXT NOT NULL,
                args_json TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL,
                risk TEXT, approval_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 1, result_json TEXT, verified INTEGER,
                note TEXT, error TEXT, started_at TEXT, finished_at TEXT,
                PRIMARY KEY (task_id, idx));
            CREATE TABLE IF NOT EXISTS computer_task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL,
                kind TEXT NOT NULL, message TEXT NOT NULL, step_idx INTEGER);
            """)

    def _event(self, task_id: str, kind: str, message: str, step_idx: int | None = None) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO computer_task_events(task_id,at,kind,message,step_idx) VALUES (?,?,?,?,?)",
                (task_id, _now(), kind, message[:400], step_idx))

    def _set_task(self, task_id: str, **fields: Any) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE computer_tasks SET {cols} WHERE task_id=?",
                       (*fields.values(), task_id))

    def _set_step(self, task_id: str, idx: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE computer_task_steps SET {cols} WHERE task_id=? AND idx=?",
                       (*fields.values(), task_id, idx))

    # ---- plan -----------------------------------------------------------------------------
    def create_task(self, title: str, steps: list[dict[str, Any]], *, source: str = "text") -> dict[str, Any]:
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200:
            raise TaskPlanError("title must be 1-200 characters")
        if source not in {"text", "voice", "brain"}:
            raise TaskPlanError("source must be text, voice or brain")
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise TaskPlanError(f"a plan needs 1-{MAX_STEPS} steps")
        prepared = []
        for index, raw in enumerate(steps, start=1):
            if not isinstance(raw, dict) or set(raw) - {"capability", "args", "description"}:
                raise TaskPlanError(f"step {index}: only capability, args and description are allowed")
            capability = raw.get("capability")
            handler = self.handlers.get(capability) if isinstance(capability, str) else None
            if handler is None:
                raise TaskPlanError(f"step {index}: unknown or unavailable capability {capability!r}")
            args = raw.get("args", {})
            if not isinstance(args, dict):
                raise TaskPlanError(f"step {index}: args must be an object")
            missing = [k for k in handler.required if k not in args]
            if missing:
                raise TaskPlanError(f"step {index}: missing argument(s) {', '.join(missing)}")
            for value in json.dumps(args).split('"'):
                for match in _TEMPLATE.finditer(value):
                    if int(match.group(1)) >= index:
                        raise TaskPlanError(f"step {index}: can only reference earlier steps")
            description = str(raw.get("description") or capability)[:200]
            prepared.append((index, capability, args, description, handler))
        task_id = uuid.uuid4().hex
        now = _now()
        with self._db() as db:
            db.execute("INSERT INTO computer_tasks(task_id,title,source,status,created_at,updated_at)"
                       " VALUES (?,?,?,?,?,?)", (task_id, title.strip(), source, "planned", now, now))
            for index, capability, args, description, handler in prepared:
                db.execute(
                    "INSERT INTO computer_task_steps(task_id,idx,capability,args_json,description,"
                    "status,max_attempts) VALUES (?,?,?,?,?,?,?)",
                    (task_id, index, capability, json.dumps(args), description, "pending",
                     3 if handler.idempotent else 1))
        self._event(task_id, "created", f"Task planned with {len(prepared)} step(s)")
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._db() as db:
            task = db.execute("SELECT * FROM computer_tasks WHERE task_id=?", (task_id,)).fetchone()
            if task is None:
                raise KeyError("Unknown task")
            steps = db.execute("SELECT * FROM computer_task_steps WHERE task_id=? ORDER BY idx",
                               (task_id,)).fetchall()
        result = dict(task)
        result["steps"] = [self._decode_step(s) for s in steps]
        result["progress"] = {
            "total": len(steps),
            "succeeded": sum(1 for s in steps if s["status"] == "succeeded"),
        }
        return result

    @staticmethod
    def _decode_step(row: sqlite3.Row) -> dict[str, Any]:
        step = dict(row)
        step["args"] = json.loads(step.pop("args_json"))
        raw_result = step.pop("result_json")
        step["result"] = json.loads(raw_result) if raw_result else None
        if step["verified"] is not None:
            step["verified"] = bool(step["verified"])
        return step

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT task_id,title,source,status,outcome,created_at,updated_at "
                              "FROM computer_tasks ORDER BY created_at DESC LIMIT ?",
                              (max(1, min(limit, 200)),)).fetchall()
        return [dict(r) for r in rows]

    def events(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT at,kind,message,step_idx FROM computer_task_events "
                              "WHERE task_id=? ORDER BY id DESC LIMIT ?",
                              (task_id, max(1, min(limit, 500)))).fetchall()
        return [dict(r) for r in reversed(rows)]

    def _permission_decision(
        self, capability: str, args: dict[str, Any], info: Inspection
    ) -> Any | None:
        """Apply the shared owner policy to capabilities with a real approval path.

        Terminal execution already consults PermissionPolicy directly. Steps with
        no approval adapter keep their existing read-only or narrowly-scoped flow.
        """
        if (
            self.permission_policy is None
            or self.capability_registry is None
            or capability == "terminal.run"
        ):
            return None
        handler = self.handlers[capability]
        try:
            spec = self.capability_registry.get(capability)
        except KeyError:
            return Decision(
                allowed=False,
                requires_approval=True,
                reason="capability has no registered owner permission specification",
            )
        path = self.permission_path(capability, args) if self.permission_path else None
        decision = self.permission_policy.authorize(spec, path=path)
        # Adapter inspection can identify a consequential target more precisely
        # than a generic catalogue row. Never let a lower-risk grant waive it.
        if info.risk == "high" and decision.allowed and not decision.requires_approval:
            decision = replace(
                decision,
                requires_approval=True,
                reason="this target is high risk; per-action approval is mandatory",
            )
        # A capability without a one-time approval adapter cannot use a
        # permission prompt to authorize its side effect. It must have an active
        # matching owner grant; otherwise fail closed before the handler runs.
        if (
            decision.allowed
            and decision.requires_approval
            and not handler.idempotent
            and handler.request_approval is None
        ):
            decision = replace(
                decision,
                allowed=False,
                reason="this capability needs an owner grant; no per-action approval adapter is available",
            )
        return decision

    def plan_summary(self, task_id: str) -> list[dict[str, Any]]:
        """What the owner is about to be asked to approve, from current state, side-effect free."""
        out = []
        for step in self.get_task(task_id)["steps"]:
            handler = self.handlers[step["capability"]]
            try:
                resolved = self._resolve_args(task_id, step["args"], strict=False)
                info = handler.inspect(resolved)
                permission = self._permission_decision(step["capability"], resolved, info)
                needs_approval = (
                    permission.requires_approval if permission is not None else info.needs_approval
                )
                summary = info.summary
                if permission is not None and not permission.allowed:
                    summary = f"PERMISSION DENIED: {permission.reason}; {summary}"
                out.append({"step": step["idx"], "capability": step["capability"],
                            "description": step["description"], "risk": info.risk,
                            "needs_approval": needs_approval, "summary": summary})
            except Exception as exc:
                out.append({"step": step["idx"], "capability": step["capability"],
                            "description": step["description"], "risk": "unknown",
                            "needs_approval": True, "summary": f"cannot be checked now: {exc}"})
        return out

    # ---- recovery and control ---------------------------------------------------------------
    def recover(self) -> list[str]:
        """Call at startup. Nothing is resumed; in-flight work is marked as such."""
        interrupted = []
        with self._db() as db:
            tasks = db.execute("SELECT task_id FROM computer_tasks WHERE status='running'").fetchall()
        for row in tasks:
            task_id = row["task_id"]
            with self._db() as db:
                db.execute("UPDATE computer_task_steps SET status='interrupted', "
                           "note='The application stopped while this step was running; its outcome is unknown.' "
                           "WHERE task_id=? AND status='running'", (task_id,))
            self._set_task(task_id, status="interrupted")
            self._event(task_id, "interrupted", "Application restarted while the task was running")
            interrupted.append(task_id)
        return interrupted

    def on_cancel(self, callback: Callable[[str], Any]) -> None:
        """Register a callable(task_id) that stops in-flight work of a cancelled task."""
        self._cancel_callbacks.append(callback)

    def pause(self, task_id: str) -> None:
        self._require_open(task_id)
        self._set_task(task_id, pause_requested=1)
        self._event(task_id, "pause_requested", "Owner requested a pause")

    def cancel(self, task_id: str) -> None:
        task = self._require_open(task_id)
        self._set_task(task_id, cancel_requested=1)
        self._event(task_id, "cancel_requested", "Owner requested cancellation")
        for callback in self._cancel_callbacks:
            try:
                callback(task_id)
            except Exception:
                pass
        if task["status"] != "running" and task_id not in self._running:
            self._finish_cancelled(task_id)

    def retry_step(self, task_id: str, idx: int) -> None:
        """Owner decision to re-run an interrupted/failed step; it will be re-validated and re-approved."""
        task = self.get_task(task_id)
        if task["status"] in {"completed", "cancelled"}:
            raise TaskPlanError(f"Task is already {task['status']}")
        step = next((s for s in task["steps"] if s["idx"] == idx), None)
        if step is None or step["status"] not in {"interrupted", "failed"}:
            raise TaskPlanError("Only interrupted or failed steps can be retried")
        self._set_step(task_id, idx, status="pending", approval_id=None, attempts=0, error=None,
                       note=None, finished_at=None)
        with self._db() as db:
            db.execute("UPDATE computer_task_steps SET status='pending', note=NULL "
                       "WHERE task_id=? AND idx>? AND status='skipped'", (task_id, idx))
        self._set_task(task_id, status="paused", outcome=None, final_report=None)
        self._event(task_id, "step_reset", f"Owner reset step {idx} for another attempt", idx)

    def _require_open(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task["status"] in TERMINAL_TASK_STATES:
            raise TaskPlanError(f"Task is already {task['status']}")
        return task

    def _finish_cancelled(self, task_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE computer_task_steps SET status='cancelled' WHERE task_id=? "
                       "AND status IN ('pending','awaiting_approval')", (task_id,))
        self._set_task(task_id, status="cancelled", outcome="cancelled")
        self._event(task_id, "cancelled", "Task cancelled; remaining steps were not run")
        self._set_task(task_id, final_report=self.report(task_id))

    def _resolve_args(self, task_id: str, args: dict[str, Any], *, strict: bool = True) -> dict[str, Any]:
        task = self.get_task(task_id)
        results = {s["idx"]: (s["result"] or {}) for s in task["steps"]}

        def sub(value: Any) -> Any:
            if isinstance(value, str):
                def repl(match: re.Match) -> str:
                    try:
                        found = _lookup(results.get(int(match.group(1)), {}), match.group(2))
                    except KeyError:
                        if strict:
                            raise TaskPlanError(
                                f"step reference {match.group(0)} has no recorded value") from None
                        return match.group(0)
                    return str(found)
                return _TEMPLATE.sub(repl, value)
            if isinstance(value, dict):
                return {k: sub(v) for k, v in value.items()}
            if isinstance(value, list):
                return [sub(v) for v in value]
            return value
        return sub(args)

    # ---- execution --------------------------------------------------------------------------
    async def advance(self, task_id: str) -> dict[str, Any]:
        """Run steps until the task finishes or needs the owner (approval, pause, stop)."""
        task = self.get_task(task_id)
        if task["status"] in TERMINAL_TASK_STATES:
            return task
        if task_id in self._running:
            raise TaskPlanError("Task is already running")
        if any(s["status"] == "interrupted" and not self.handlers[s["capability"]].idempotent
               for s in task["steps"]):
            self._event(task_id, "needs_review",
                        "A step was interrupted and its outcome is unknown; retry it explicitly")
            return self.get_task(task_id)
        self._running.add(task_id)
        token = CURRENT_TASK_ID.set(task_id)
        try:
            await self._loop(task_id)
        finally:
            CURRENT_TASK_ID.reset(token)
            self._running.discard(task_id)
        return self.get_task(task_id)

    async def _loop(self, task_id: str) -> None:
        self._set_task(task_id, status="running", pause_requested=0)
        self._event(task_id, "running", "Task running")
        for step in self.get_task(task_id)["steps"]:
            flags = self.get_task(task_id)
            if flags["cancel_requested"]:
                self._finish_cancelled(task_id)
                return
            if flags["pause_requested"]:
                self._set_task(task_id, status="paused", pause_requested=0)
                self._event(task_id, "paused", "Task paused before the next step")
                return
            if step["status"] in {"succeeded", "skipped", "cancelled"}:
                continue
            if step["status"] == "failed":
                return self._fail(task_id, step["idx"], step["error"] or "step failed")
            if step["status"] == "interrupted":
                self._set_step(task_id, step["idx"], status="pending")
            if self.control is not None:
                try:
                    self.control.assert_active()
                except AutomationStopped as exc:
                    self._set_task(task_id, status="paused")
                    self._event(task_id, "stopped", f"Paused because automation was stopped: {exc}")
                    return
            proceed = await self._run_step(task_id, step["idx"])
            if not proceed:
                return
        self._complete(task_id)

    async def _call(self, handler: StepHandler, fn: Callable, *args: Any) -> Any:
        if handler.is_async:
            return await fn(*args)
        return await asyncio.to_thread(fn, *args)

    async def _run_step(self, task_id: str, idx: int, *, allow_reapproval: bool = True) -> bool:
        step = next(s for s in self.get_task(task_id)["steps"] if s["idx"] == idx)
        handler = self.handlers[step["capability"]]
        total = len(self.get_task(task_id)["steps"])
        try:
            args = self._resolve_args(task_id, step["args"])
            info = await self._call(handler, handler.inspect, args)  # revalidates current state
            permission = self._permission_decision(step["capability"], args, info)
            if permission is not None and not permission.allowed:
                self._fail(task_id, idx, f"Permission denied: {permission.reason}")
                self._event(
                    task_id, "permission_denied",
                    f"Step {idx} refused by owner permission policy: {permission.reason}", idx,
                )
                return False
        except AutomationStopped:
            raise
        except Exception as exc:
            self._fail(task_id, idx, f"Step cannot run: {exc}")
            return False
        self._set_step(task_id, idx, risk=info.risk)
        approval_id = step["approval_id"]
        needs_approval = (
            permission.requires_approval if permission is not None else info.needs_approval
        )
        if needs_approval:
            status = self.approvals.status(approval_id) if approval_id else None
            if approval_id is None or status in {None, "consumed", "expired", "rejected"}:
                if status == "rejected":
                    self._set_step(task_id, idx, status="failed", error="The owner rejected this step.",
                                   finished_at=_now())
                    self._fail(task_id, idx, "The owner rejected this step")
                    return False
                try:
                    approval_id = await self._call(handler, handler.request_approval, args)
                except Exception as exc:
                    self._fail(task_id, idx, f"Approval could not be requested: {exc}")
                    return False
                self._set_step(task_id, idx, status="awaiting_approval", approval_id=approval_id)
                self._set_task(task_id, status="awaiting_approval")
                self._event(task_id, "awaiting_approval",
                            f"Step {idx} of {total} needs your approval: {info.summary}", idx)
                return False
            if status == "pending":
                self._set_task(task_id, status="awaiting_approval")
                return False
        else:
            # The shared owner grant authorizes this adapter action. Create its
            # normal one-time adapter approval internally, then consume it in run().
            # High-risk actions never reach this branch.
            if permission is not None and handler.request_approval is not None:
                try:
                    approval_id = await self._call(handler, handler.request_approval, args)
                    # The standing owner grant is the authorization for this
                    # medium-risk action. Approve only the exact, freshly prepared
                    # adapter payload so the adapter can still consume its normal
                    # one-time, payload-bound token.
                    self.approvals.approve(approval_id)
                    self._set_step(task_id, idx, approval_id=approval_id)
                except Exception as exc:
                    self._fail(task_id, idx, f"Granted action could not be prepared: {exc}")
                    return False
            else:
                approval_id = None
        self._set_step(task_id, idx, status="running", started_at=_now())
        self._event(task_id, "step_started", f"Step {idx} of {total}: {step['description']}", idx)
        outcome = Outcome(ok=False, error="not run")
        for attempt in range(1, step["max_attempts"] + 1):
            self._set_step(task_id, idx, attempts=attempt)
            try:
                outcome = await self._call(handler, handler.run, args, approval_id)
            except ApprovalError:
                # State changed since approval: the approval no longer matches. Ask again, do not act.
                self._set_step(task_id, idx, status="pending", approval_id=None,
                               note="The situation changed after approval; a fresh approval is needed.")
                self._event(task_id, "approval_stale",
                            f"Step {idx}: state changed since approval; requesting a fresh one", idx)
                if allow_reapproval and info.needs_approval:
                    return await self._run_step(task_id, idx, allow_reapproval=False)
                self._fail(task_id, idx, "The step could not be authorized")
                return False
            except AutomationStopped as exc:
                self._set_step(task_id, idx, status="pending", note=str(exc))
                self._set_task(task_id, status="paused")
                self._event(task_id, "stopped", "Paused because automation was stopped", idx)
                return False
            except Exception as exc:
                outcome = Outcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
            if outcome.ok or not (outcome.retryable and handler.idempotent):
                break
        self._set_step(task_id, idx, finished_at=_now(),
                       result_json=json.dumps(outcome.data, default=str)[:20000],
                       verified=None if outcome.verified is None else int(outcome.verified),
                       note=outcome.note, error=outcome.error,
                       status="succeeded" if outcome.ok else "failed")
        if outcome.ok:
            tag = {True: "verified", False: "NOT verified", None: "no verification available"}[outcome.verified]
            self._event(task_id, "step_done", f"Step {idx} done ({tag})", idx)
            return True
        self._fail(task_id, idx, outcome.error or "step failed")
        return False

    def _fail(self, task_id: str, idx: int, reason: str) -> None:
        if self.get_task(task_id)["cancel_requested"]:
            # The step was cut short because the owner cancelled; that is a cancellation, not a failure.
            self._set_step(task_id, idx, status="cancelled", error=None, finished_at=_now(),
                           note="Stopped because the task was cancelled.")
            self._finish_cancelled(task_id)
            return
        self._set_step(task_id, idx, status="failed", error=reason[:300],
                       finished_at=_now())
        with self._db() as db:
            db.execute("UPDATE computer_task_steps SET status='skipped', "
                       "note='Not run because an earlier step failed.' "
                       "WHERE task_id=? AND idx>? AND status='pending'", (task_id, idx))
        self._set_task(task_id, status="failed", outcome="failed")
        self._event(task_id, "failed", f"Step {idx} failed: {reason}", idx)
        self._set_task(task_id, final_report=self.report(task_id))

    def _complete(self, task_id: str) -> None:
        steps = self.get_task(task_id)["steps"]
        done = [s for s in steps if s["status"] == "succeeded"]
        unverified = [s for s in done if s["verified"] is False]
        unknown = [s for s in done if s["verified"] is None]
        outcome = "verified" if not unverified and not unknown else "partially_verified" \
            if len(unverified) + len(unknown) < len(done) else "unverified"
        self._set_task(task_id, status="completed", outcome=outcome)
        self._event(task_id, "completed", f"Task finished ({outcome})")
        self._set_task(task_id, final_report=self.report(task_id))

    # ---- background execution -----------------------------------------------------------------
    def start_background(self, task_id: str) -> None:
        existing = self._background.get(task_id)
        if existing and not existing.done():
            raise TaskPlanError("Task is already running")
        self._background[task_id] = asyncio.get_running_loop().create_task(self.advance(task_id))

    # ---- report -------------------------------------------------------------------------------
    def report(self, task_id: str) -> str:
        """Plain-language outcome built only from recorded step data."""
        task = self.get_task(task_id)
        lines = [f"Task: {task['title']} ({task['status']}"
                 + (f", {task['outcome']}" if task.get("outcome") else "") + ")"]
        for s in task["steps"]:
            mark = {"succeeded": "done", "failed": "FAILED", "skipped": "not run",
                    "cancelled": "cancelled", "pending": "not started",
                    "awaiting_approval": "waiting for approval", "running": "running",
                    "interrupted": "outcome unknown (interrupted)"}[s["status"]]
            detail = ""
            if s["status"] == "succeeded":
                detail = {True: ", verified", False: ", NOT verified", None: ", not independently verifiable"}[s["verified"]]
            elif s["error"]:
                detail = f": {s['error']}"
            if s["note"]:
                detail += f" ({s['note']})"
            lines.append(f"  {s['idx']}. {s['description']} - {mark}{detail}")
        return "\n".join(lines)
