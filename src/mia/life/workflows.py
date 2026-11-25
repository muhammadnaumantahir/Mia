"""Bounded workflow composer and checkpoint runtime for Mia's own data (MIA-LIFE-08, A7).

A workflow is an ordered list of typed steps over *internal, non-destructive* actions: reading
and adding tasks/notes, completing a task (reversible by reopening) and queueing a notification.
It reuses the proven ``TaskEngine`` (SQLite checkpoint after every step transition, pause, cancel,
explicit retry, automation stop-all, "interrupted step means unknown, never silently re-run")
instead of a second runtime, with its own database so computer tasks and workflows never mix.

Governance, by construction:

* Only the handlers registered here exist. Anything else, in particular computer, browser,
  terminal, filesystem or desktop capabilities and any delete, is refused when the plan is
  created, with a message saying so. Those need the central permission broker (MIA-COMP-04)
  and approvals, and must not be reachable through a workflow by naming them.
* No step can grant permission or approve anything: handlers never touch approvals or grants.
* ``preview`` shows what each step would do from current state without changing anything.
* Each step has a wall-clock limit. A step that times out is reported as failed with unknown
  outcome (a worker thread cannot be killed), never as success.
* ``${steps.N.field}`` lets a later step use an earlier step's recorded result.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mia.computer.approvals import ApprovalStore
from mia.computer.control import CURRENT_TASK_ID, AutomationControl
from mia.computer.tasks import (
    Inspection,
    Outcome,
    StepHandler,
    TaskEngine,
    TaskPlanError,
)

def explicit_workflow_execution_requested(prompt: str) -> bool:
    """Fail closed unless the current user turn clearly asks to execute an action."""
    text = re.sub(r"\s+", " ", str(prompt or "").strip().lower())[:3000]
    if not text:
        return False
    deferred = (
        r"\b(?:don't|do not|dont|never|no need to)\s+(?:run|execute|start|perform|carry out)\b",
        r"\b(?:don't|do not|dont|never|no need to)\s+(?:do it|add|create|save|complete|mark|set)\b",
        r"\b(?:don't|do not|dont)\s+(?:want|need) to\s+(?:run|execute|start|add|create|save|complete|mark|set)\b",
        r"\b(?:maybe|might|may be|considering|thinking about)\b.{0,60}\b(?:run|execute|start|add|create|save|complete|mark|set)\b",
        r"\b(?:run|execute|start|perform|carry out)\b.{0,60}\b(?:after|once|when)\b.{0,60}\b(?:approve|approval|confirm|confirmation|say go)\b",
        r"\b(?:plan|preview|draft)\s+only\b",
        r"\bnot yet\b",
        r"\b(?:what if|how do i|how can i|can i|could i|should i)\b.{0,60}\b(?:run|execute|start|add|create)\b",
        r"\brun me through\b|\brun through (?:the )?(?:plan|workflow)\b",
        r"\b(?:explain|describe|define|what does|teach me|show me how to|how to|how does)\b.{0,80}\b(?:run|execute|start|workflow|task)\b",
        r"\b(?:mat chalao|abhi mat|abhi na chalao|sirf plan|sirf preview)\b",
    )
    if any(re.search(pattern, text) for pattern in deferred):
        return False
    explicit_run = (
        r"\b(?:run|execute|start|perform|carry out)\b.{0,60}\b(?:workflow|plan|task|steps|it|this|that)\b",
        r"\b(?:run|execute|start|perform)\s+(?:it|this|that)\b",
        r"\bgo ahead and\s+(?:run|execute|start|perform)\b",
        r"\bdo it\b",
        r"\b(?:chalao|chala do|chala dein|run kar do|execute kar do)\b",
    )
    if any(re.search(pattern, text) for pattern in explicit_run):
        return True
    # Direct internal actions (e.g. "add a task") count as execution requests, but
    # requests to plan/design/preview remain non-executing unless they also say run.
    if re.search(r"\b(?:plan|preview|draft|design)\b", text):
        return False
    direct_action = (
        r"\b(?:add|create|save|complete|mark)\b.{0,50}\b(?:task|reminder|note|notification|item)\b",
        r"\bset\s+(?:a\s+)?reminder\b",
        r"\bremind me\b",
    )
    return any(re.search(pattern, text) for pattern in direct_action)


STEP_TIMEOUT_SECONDS = 20.0
_ACTIVE_WORKFLOW_STEP: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "mia_active_workflow_step", default=None
)
_EXTERNAL_PREFIXES = ("computer.", "browser.", "terminal.", "filesystem.", "desktop.", "apps.", "process.")
_ADD_KEYS = {"kind", "title", "details", "due_at", "project", "priority"}
_LIST_KEYS = {"kind", "status", "project", "limit"}


class WorkflowEngine(TaskEngine):
    """TaskEngine restricted to internal handlers, with recoverable per-step time limits."""

    def __init__(self, *args: Any, step_timeout: float = STEP_TIMEOUT_SECONDS, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.step_timeout = step_timeout
        # A timed-out asyncio wait does not stop a worker thread. Keep its task alive so
        # reconciliation can observe the real result instead of accidentally running twice.
        self._timed_out_workers: dict[tuple[str, int], asyncio.Task[Any]] = {}

    def create_task(self, title: str, steps: list[dict[str, Any]], *, source: str = "text") -> dict[str, Any]:
        if isinstance(steps, list):
            for index, raw in enumerate(steps, start=1):
                capability = raw.get("capability") if isinstance(raw, dict) else None
                if isinstance(capability, str) and capability not in self.handlers:
                    if capability.startswith(_EXTERNAL_PREFIXES) or "delete" in capability:
                        raise TaskPlanError(
                            f"step {index}: {capability!r} is an external or destructive action; "
                            "workflows only run internal, non-destructive steps for now"
                        )
        return super().create_task(title, steps, source=source)

    async def _call(self, handler: StepHandler, fn: Callable, *args: Any) -> Any:
        # Inspection is read-only; the timeout/reconciliation contract applies to the
        # execution call, where a synchronous worker thread may outlive its awaiter.
        if fn != handler.run:
            return await super()._call(handler, fn, *args)
        task_id = CURRENT_TASK_ID.get()
        step = _ACTIVE_WORKFLOW_STEP.get()
        if not task_id or step is None:
            return await super()._call(handler, fn, *args)
        worker = asyncio.create_task(fn(*args) if handler.is_async else asyncio.to_thread(fn, *args))
        try:
            return await asyncio.wait_for(asyncio.shield(worker), timeout=self.step_timeout)
        except TimeoutError as exc:
            self._timed_out_workers[(task_id, step)] = worker
            raise TimeoutError(
                f"step exceeded {self.step_timeout:g}s; outcome unknown; reconcile before retrying"
            ) from exc

    async def _run_step(self, task_id: str, idx: int, *, allow_reapproval: bool = True) -> bool:
        token = _ACTIVE_WORKFLOW_STEP.set(idx)
        try:
            proceed = await super()._run_step(task_id, idx, allow_reapproval=allow_reapproval)
        finally:
            _ACTIVE_WORKFLOW_STEP.reset(token)

        key = (task_id, idx)
        if key not in self._timed_out_workers:
            return proceed

        # TaskEngine records raised exceptions as failed. Override that generic state:
        # the side effect may still be running, so the only truthful state is interrupted.
        self._set_step(
            task_id, idx, status="interrupted",
            error="Step timed out; outcome is unknown until reconciliation.",
            note="The worker may still be running. Do not retry while its outcome is unresolved.",
        )
        with self._db() as db:
            db.execute(
                "UPDATE computer_task_steps SET status='pending', note=NULL "
                "WHERE task_id=? AND idx>? AND status='skipped'",
                (task_id, idx),
            )
        self._set_task(task_id, status="interrupted", outcome="unknown", final_report=None)
        self._event(task_id, "step_outcome_unknown",
                    f"Step {idx} timed out; waiting for worker reconciliation", idx)
        self._set_task(task_id, final_report=self.report(task_id))
        return False

    async def advance(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        # Workflow retries are never automatic after an interrupted/unknown step,
        # including read-only steps: reconciliation must happen first.
        interrupted = next((s for s in task["steps"] if s["status"] == "interrupted"), None)
        if interrupted is not None:
            self._event(task_id, "needs_reconciliation",
                        f"Step {interrupted['idx']} has an unresolved outcome; reconcile it before continuing",
                        interrupted["idx"])
            return task
        return await super().advance(task_id)

    def cancel(self, task_id: str) -> None:
        task = self._require_open(task_id)
        if any(step["status"] == "interrupted" for step in task["steps"]):
            # Do not report cancellation while a timed-out worker may still complete.
            self._set_task(task_id, cancel_requested=1)
            self._event(task_id, "cancel_requested",
                        "Cancellation queued until the unknown step is reconciled")
            return
        super().cancel(task_id)

    def retry_step(self, task_id: str, idx: int) -> None:
        step = next((s for s in self.get_task(task_id)["steps"] if s["idx"] == idx), None)
        if step is not None and step["status"] == "interrupted":
            raise TaskPlanError("Interrupted workflow steps must be reconciled before retry")
        super().retry_step(task_id, idx)

    def reconcile_timeout(
        self, task_id: str, idx: int, *, decision: Literal["applied", "not_applied"] | None = None
    ) -> dict[str, Any]:
        """Reconcile an unknown step; never retries while its original worker is still alive."""
        task = self.get_task(task_id)
        step = next((s for s in task["steps"] if s["idx"] == idx), None)
        if step is None or step["status"] != "interrupted":
            raise TaskPlanError("Only an interrupted workflow step can be reconciled")

        key = (task_id, idx)
        worker = self._timed_out_workers.get(key)
        if worker is not None:
            if not worker.done():
                return {"status": "waiting", "message": "The original worker is still running; retry is refused."}
            try:
                outcome = worker.result()
            except Exception as exc:
                self._set_step(task_id, idx, status="failed",
                               error=f"Worker finished with {type(exc).__name__}: {exc}"[:300],
                               note="Inspect the target state before choosing an explicit retry.",
                               finished_at=_now())
                self._set_task(task_id, status="failed", outcome="unknown", final_report=None)
                self._event(task_id, "step_reconciliation_error",
                            f"Step {idx} worker ended with an exception; owner inspection is required", idx)
            else:
                if not isinstance(outcome, Outcome):
                    self._set_step(task_id, idx, status="failed",
                                   error="Worker returned an invalid outcome.",
                                   note="Inspect the target state before choosing an explicit retry.",
                                   finished_at=_now())
                    self._set_task(task_id, status="failed", outcome="unknown", final_report=None)
                elif outcome.ok:
                    self._set_step(
                        task_id, idx, status="succeeded",
                        result_json=json.dumps(outcome.data, default=str)[:20000],
                        verified=None if outcome.verified is None else int(outcome.verified),
                        note=outcome.note, error=None, finished_at=_now(),
                    )
                    self._set_task(task_id, status="paused", outcome=None, final_report=None)
                    self._event(task_id, "step_reconciled_applied",
                                f"Step {idx} completed after its timeout; recorded worker result", idx)
                else:
                    self._set_step(task_id, idx, status="failed",
                                   result_json=json.dumps(outcome.data, default=str)[:20000],
                                   verified=None if outcome.verified is None else int(outcome.verified),
                                   error=outcome.error or "Worker reported an unsuccessful outcome.",
                                   note=outcome.note or "Inspect the target state before choosing an explicit retry.",
                                   finished_at=_now())
                    self._set_task(task_id, status="failed", outcome="unknown", final_report=None)
                    self._event(task_id, "step_reconciled_failed",
                                f"Step {idx} worker finished unsuccessfully; owner review is required", idx)
            self._timed_out_workers.pop(key, None)
            if self.get_task(task_id)["cancel_requested"]:
                self._finish_cancelled(task_id)
            else:
                self._set_task(task_id, final_report=self.report(task_id))
            return self.get_task(task_id)

        # After process restart the worker result is unavailable. The owner must inspect
        # the target and explicitly confirm whether the action happened before proceeding.
        if decision == "applied":
            self._set_step(task_id, idx, status="succeeded", result_json="{}", verified=None,
                           error=None, note="Owner confirmed the action was applied after restart; output data is unavailable.",
                           finished_at=_now())
            self._set_task(task_id, status="paused", outcome=None, final_report=None)
            self._event(task_id, "step_reconciled_owner_applied",
                        f"Owner confirmed step {idx} was applied after restart", idx)
        elif decision == "not_applied":
            # This is the sole path allowed to re-arm an interrupted workflow step.
            super().retry_step(task_id, idx)
            self._event(task_id, "step_reconciled_owner_retry",
                        f"Owner confirmed step {idx} was not applied; explicit retry is now available", idx)
        else:
            raise TaskPlanError("After restart, decision must be 'applied' or 'not_applied'")
        if self.get_task(task_id)["cancel_requested"]:
            self._finish_cancelled(task_id)
        else:
            self._set_task(task_id, final_report=self.report(task_id))
        return self.get_task(task_id)


def _text(args: dict[str, Any], key: str, limit: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be non-empty text")
    return value.strip()[:limit]


def build_workflow_handlers(*, personal: Any, notifications: Any | None = None) -> dict[str, StepHandler]:
    handlers: dict[str, StepHandler] = {}

    def add(handler: StepHandler) -> None:
        handlers[handler.capability] = handler

    def clean_add_args(args: dict[str, Any]) -> dict[str, Any]:
        extra = set(args) - _ADD_KEYS
        if extra:
            raise ValueError(f"unsupported argument(s): {', '.join(sorted(extra))}")
        _text(args, "title", 300)
        return dict(args)

    def run_add(args: dict[str, Any], _approval: str | None) -> Outcome:
        try:
            item_id = personal.add(actor="workflow", **clean_add_args(args))
        except Exception as exc:  # duplicate, validation: reported, never retried
            return Outcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
        stored = personal.get(item_id)
        return Outcome(ok=stored is not None, verified=stored is not None,
                       data={"item_id": item_id}, error=None if stored else "item was not found after adding")

    def inspect_add(args: dict[str, Any]) -> Inspection:
        checked = clean_add_args(args)
        return Inspection(f"Add {checked.get('kind')}: {str(checked['title'])[:80]}")

    add(StepHandler(capability="personal.add", required=("kind", "title"), inspect=inspect_add, run=run_add))

    def inspect_list(args: dict[str, Any]) -> Inspection:
        extra = set(args) - _LIST_KEYS
        if extra:
            raise ValueError(f"unsupported argument(s): {', '.join(sorted(extra))}")
        return Inspection("List your items (read only)")

    def run_list(args: dict[str, Any], _approval: str | None) -> Outcome:
        inspect_list(args)
        limit = max(1, min(int(args.get("limit", 20)), 50))
        rows = personal.list(kind=args.get("kind"), status=args.get("status") or "open",
                             project=args.get("project"), limit=limit)
        items = [{"item_id": r["item_id"], "title": r["title"], "kind": r["kind"], "status": r["status"]}
                 for r in rows]
        return Outcome(ok=True, verified=True, data={"items": items, "count": len(items)})

    add(StepHandler(capability="personal.list", idempotent=True, inspect=inspect_list, run=run_list))

    def inspect_complete(args: dict[str, Any]) -> Inspection:
        item_id = _text(args, "item_id", 64)
        item = personal.get(item_id)
        if item is None:
            raise ValueError("that item does not exist")
        if item["status"] != "open":
            raise ValueError(f"that item is already {item['status']}")
        return Inspection(f"Mark done (reversible by reopening): {item['title'][:80]}")

    def run_complete(args: dict[str, Any], _approval: str | None) -> Outcome:
        inspect_complete(args)
        item_id = _text(args, "item_id", 64)
        personal.complete(item_id, actor="workflow")
        done = (personal.get(item_id) or {}).get("status") != "open"
        return Outcome(ok=done, verified=done, data={"item_id": item_id},
                       error=None if done else "item is still open after completing")

    add(StepHandler(capability="personal.complete", required=("item_id",),
                    inspect=inspect_complete, run=run_complete))

    if notifications is not None:
        def inspect_notify(args: dict[str, Any]) -> Inspection:
            return Inspection(f"Queue a notification: {_text(args, 'title', 120)[:80]}")

        def run_notify(args: dict[str, Any], _approval: str | None) -> Outcome:
            task_id = CURRENT_TASK_ID.get() or "adhoc"
            title = _text(args, "title", 120)
            result = notifications.submit(
                source_type="workflow", source_ref=task_id, title=title,
                body=_text(args, "body", 500), reason="Step of a workflow you composed",
                dedupe_key=f"workflow:{task_id}:{title}",
            )
            return Outcome(ok=True, verified=None,
                           data={"decision": result["decision"]},
                           note="Delivery follows your notification and quiet-hours settings")

        add(StepHandler(capability="notify.submit", required=("title", "body"),
                        inspect=inspect_notify, run=run_notify))
    return handlers


class WorkflowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=3, max_length=60)
    args: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", max_length=200)


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    steps: list[WorkflowStep] = Field(min_length=1, max_length=20)


class WorkflowRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1, le=20)


class WorkflowReconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=1, le=20)
    decision: Literal["applied", "not_applied"] | None = None


def register_workflow_routes(app: FastAPI, engine: WorkflowEngine, record: Callable[..., Any]) -> None:
    """Register on the app itself (not include_router) so route enumeration stays flat."""
    base = "/api/workflows"

    def error(exc: Exception) -> HTTPException:
        if isinstance(exc, KeyError):
            return HTTPException(status_code=404, detail="Unknown workflow")
        return HTTPException(status_code=422, detail=str(exc))

    @app.get(base + "/capabilities")
    def capabilities() -> dict[str, Any]:
        return {"items": sorted(engine.handlers)}

    @app.post(base + "", status_code=201)
    def create(item: WorkflowCreate) -> dict[str, Any]:
        try:
            task = engine.create_task(item.title, [s.model_dump() for s in item.steps], source="text")
        except TaskPlanError as exc:
            raise error(exc) from exc
        record(event_type="workflow_created", summary=f"Workflow planned: {item.title[:80]} ({len(item.steps)} steps)",
               outcome="success", payload={"workflow_id": task["task_id"]})
        return {"workflow": task, "preview": engine.plan_summary(task["task_id"])}

    @app.get(base + "")
    def listing(limit: int = 50) -> dict[str, Any]:
        return {"items": engine.list_tasks(max(1, min(limit, 200)))}

    @app.get(base + "/{workflow_id}")
    def get(workflow_id: str) -> dict[str, Any]:
        try:
            return engine.get_task(workflow_id)
        except KeyError as exc:
            raise error(exc) from exc

    @app.get(base + "/{workflow_id}/preview")
    def preview(workflow_id: str) -> dict[str, Any]:
        try:
            return {"preview": engine.plan_summary(workflow_id)}
        except KeyError as exc:
            raise error(exc) from exc

    @app.get(base + "/{workflow_id}/events")
    def events(workflow_id: str) -> dict[str, Any]:
        try:
            engine.get_task(workflow_id)
        except KeyError as exc:
            raise error(exc) from exc
        return {"items": engine.events(workflow_id)}

    @app.get(base + "/{workflow_id}/report")
    def report(workflow_id: str) -> dict[str, Any]:
        try:
            task = engine.get_task(workflow_id)
        except KeyError as exc:
            raise error(exc) from exc
        return {"status": task["status"], "outcome": task["outcome"], "report": engine.report(workflow_id)}

    @app.post(base + "/{workflow_id}/run")
    async def run(workflow_id: str) -> dict[str, Any]:
        try:
            task = await engine.advance(workflow_id)
        except (KeyError, TaskPlanError) as exc:
            raise error(exc) from exc
        if task["status"] in {"completed", "failed", "cancelled"}:
            record(event_type="workflow_finished", summary=f"Workflow {task['status']} ({task['outcome']})",
                   outcome="success" if task["status"] == "completed" else "failure",
                   payload={"workflow_id": workflow_id, "outcome": task["outcome"]})
        return task

    @app.post(base + "/{workflow_id}/pause")
    def pause(workflow_id: str) -> dict[str, Any]:
        try:
            engine.pause(workflow_id)
        except (KeyError, TaskPlanError) as exc:
            raise error(exc) from exc
        return {"status": "pause_requested"}

    @app.post(base + "/{workflow_id}/cancel")
    def cancel(workflow_id: str) -> dict[str, Any]:
        try:
            engine.cancel(workflow_id)
        except (KeyError, TaskPlanError) as exc:
            raise error(exc) from exc
        return {"status": "cancel_requested"}

    @app.post(base + "/{workflow_id}/retry-step")
    def retry(workflow_id: str, item: WorkflowRetry) -> dict[str, Any]:
        try:
            engine.retry_step(workflow_id, item.step)
        except (KeyError, TaskPlanError) as exc:
            raise error(exc) from exc
        return engine.get_task(workflow_id)

    @app.post(base + "/{workflow_id}/reconcile-step")
    def reconcile(workflow_id: str, item: WorkflowReconcile) -> dict[str, Any]:
        try:
            return engine.reconcile_timeout(workflow_id, item.step, decision=item.decision)
        except (KeyError, TaskPlanError) as exc:
            raise error(exc) from exc



def make_workflow_engine(
    database_path: str | Path, approvals: ApprovalStore, *, personal: Any, notifications: Any | None = None,
    control: AutomationControl | None = None, step_timeout: float = STEP_TIMEOUT_SECONDS,
) -> WorkflowEngine:
    engine = WorkflowEngine(
        database_path, approvals, build_workflow_handlers(personal=personal, notifications=notifications),
        control=control, step_timeout=step_timeout,
    )
    engine.recover()
    return engine
