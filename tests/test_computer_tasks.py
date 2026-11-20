import asyncio
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.browser import BrowserAccessError, BrowserController
from mia.computer.browser_actions import BrowserActions
from mia.computer.capabilities import PermissionLevel, default_registry
from mia.computer.control import AutomationControl
from mia.computer.fileops import FileOperations
from mia.computer.filesystem import FilesystemAdapter
from mia.computer.permissions import PermissionPolicy
from mia.computer.processes import ProcessManager
from mia.computer.task_handlers import build_handlers
from mia.computer.tasks import Inspection, Outcome, StepHandler, TaskEngine, TaskPlanError
from mia.computer.terminal import TerminalExecutor
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def run(coro):
    return asyncio.run(coro)


class Fakes:
    """Fake handlers with counters so tests can prove what did and did not run."""

    def __init__(self, approvals):
        self.approvals = approvals
        self.runs: dict[str, int] = {}
        self.flaky_failures = 1
        self.on_run = {}
        self.stale_once = False

    def bump(self, name):
        self.runs[name] = self.runs.get(name, 0) + 1
        return self.runs[name]

    def handlers(self):
        h = {}

        def ro(name, fn, idempotent=True, required=()):
            def runner(args, _a):
                n = self.bump(name)
                if name in self.on_run:
                    self.on_run[name]()
                return fn(args, n)
            h[name] = StepHandler(name, lambda a: Inspection(f"{name} {a}"), runner,
                                  idempotent=idempotent, required=required)

        ro("echo", lambda a, n: Outcome(True, True, {"items": [{"name": a.get("v", "x")}],
                                                   "value": a.get("v")}), required=())
        ro("unverified", lambda a, n: Outcome(True, False, {"v": 1}, note="could not confirm"))
        ro("unknowable", lambda a, n: Outcome(True, None, {"v": 1}))
        ro("flaky", lambda a, n: Outcome(True, True, {"attempt": n}) if n > self.flaky_failures
           else Outcome(False, error="transient", retryable=True))
        ro("breaks", lambda a, n: Outcome(False, error="it broke"), idempotent=True)
        ro("breaks_once_nonidem", lambda a, n: Outcome(False, error="nope", retryable=True),
           idempotent=False)

        def danger_run(args, approval_id):
            self.bump("danger")
            if self.stale_once:
                self.stale_once = False
                raise ApprovalError("payload changed")
            self.approvals.consume(approval_id=approval_id, action_id="fake.danger",
                                   payload={"v": args.get("v", 0)})
            if "danger" in self.on_run:
                self.on_run["danger"]()
            return Outcome(True, True, {"done": True, "recovery_id": "abc123"})

        h["danger"] = StepHandler(
            "danger", lambda a: Inspection(f"danger {a.get('v', 0)}", "high", True), danger_run,
            request_approval=lambda a: self.approvals.request(
                action_id="fake.danger", payload={"v": a.get("v", 0)}, summary="danger")["approval_id"],
            idempotent=False)
        return h


@pytest.fixture
def rig(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fakes = Fakes(approvals)
    control = AutomationControl()
    engine = TaskEngine(tmp_path / "t.sqlite3", approvals, fakes.handlers(), control=control)
    return engine, approvals, fakes, control, tmp_path


def step(cap, **args):
    return {"capability": cap, "args": args, "description": f"do {cap}"}


def test_plan_validation(rig):
    engine, *_ = rig
    bad = [
        ("", [step("echo")]), ("t", []), ("t", [step("echo")] * 21),
        ("t", [step("nonexistent")]), ("t", [{"capability": "echo", "extra": 1}]),
        ("t", [{"capability": "echo", "args": "x"}]),
        ("t", [step("echo", v="${steps.2.value}"), step("echo")]),  # forward reference
        ("t", [step("echo", v="${steps.1.value}")]),               # self reference
    ]
    for title, steps in bad:
        with pytest.raises(TaskPlanError):
            engine.create_task(title, steps)
    with pytest.raises(TaskPlanError):
        engine.create_task("t", [step("echo")], source="hacker")
    assert engine.create_task("ok", [step("echo")])["status"] == "planned"


def test_read_only_plan_completes_with_verified_report(rig):
    engine, _, fakes, _, _ = rig
    task = engine.create_task("read stuff", [step("echo", v="a"), step("echo", v="b")])
    done = run(engine.advance(task["task_id"]))
    assert done["status"] == "completed" and done["outcome"] == "verified"
    assert [s["status"] for s in done["steps"]] == ["succeeded", "succeeded"]
    assert fakes.runs["echo"] == 2
    report = engine.report(task["task_id"])
    assert "verified" in report and "1. do echo - done, verified" in report
    kinds = [e["kind"] for e in engine.events(task["task_id"])]
    assert kinds[0] == "created" and kinds.count("step_done") == 2 and kinds[-1] == "completed"
    assert run(engine.advance(task["task_id"]))["status"] == "completed"  # no-op when done
    assert fakes.runs["echo"] == 2


def test_each_consequential_step_needs_its_own_approval(rig):
    engine, approvals, fakes, _, _ = rig
    task = engine.create_task("two dangers", [step("echo"), step("danger", v=1), step("danger", v=2)])
    plan = engine.plan_summary(task["task_id"])
    assert [p["needs_approval"] for p in plan] == [False, True, True]
    assert plan[1]["risk"] == "high"

    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "awaiting_approval" and t["steps"][1]["status"] == "awaiting_approval"
    first = t["steps"][1]["approval_id"]
    assert fakes.runs.get("danger") is None  # nothing consequential ran before approval
    t = run(engine.advance(task["task_id"]))  # advancing again does not self-approve
    assert t["status"] == "awaiting_approval" and t["steps"][1]["approval_id"] == first
    assert len(approvals.list_pending()) == 1

    approvals.approve(first)
    t = run(engine.advance(task["task_id"]))
    assert t["steps"][1]["status"] == "succeeded"
    assert t["status"] == "awaiting_approval" and t["steps"][2]["status"] == "awaiting_approval"
    assert t["steps"][2]["approval_id"] != first  # approving step 2 did not approve step 3
    assert fakes.runs["danger"] == 1
    approvals.approve(t["steps"][2]["approval_id"])
    assert run(engine.advance(task["task_id"]))["status"] == "completed"


def test_rejected_approval_fails_task_and_skips_rest(rig):
    engine, approvals, fakes, _, _ = rig
    task = engine.create_task("t", [step("danger", v=1), step("echo")])
    t = run(engine.advance(task["task_id"]))
    approvals.reject(t["steps"][0]["approval_id"])
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "failed" and t["steps"][1]["status"] == "skipped"
    assert "rejected" in t["steps"][0]["error"] and fakes.runs.get("danger") is None
    assert "FAILED" in engine.report(task["task_id"]) and "not run" in engine.report(task["task_id"])


def test_state_change_after_approval_requests_fresh_approval_and_does_not_act(rig):
    engine, approvals, fakes, _, _ = rig
    task = engine.create_task("t", [step("danger", v=1)])
    t = run(engine.advance(task["task_id"]))
    old = t["steps"][0]["approval_id"]
    approvals.approve(old)
    fakes.stale_once = True  # the world changed: the approval no longer matches
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "awaiting_approval"
    assert t["steps"][0]["approval_id"] not in (None, old)
    assert "approval_stale" in [e["kind"] for e in engine.events(task["task_id"])]
    assert t["steps"][0]["status"] == "awaiting_approval"


def test_templating_between_steps(rig):
    engine, _, fakes, _, _ = rig
    task = engine.create_task("t", [step("echo", v="first"),
                                    step("echo", v="${steps.1.items.0.name}-second")])
    t = run(engine.advance(task["task_id"]))
    assert t["steps"][1]["result"]["value"] == "first-second"
    missing = engine.create_task("t2", [step("echo"), step("echo", v="${steps.1.nope}")])
    t = run(engine.advance(missing["task_id"]))
    assert t["status"] == "failed" and "no recorded value" in t["steps"][1]["error"]


def test_failure_halts_and_report_never_invents_results(rig):
    engine, _, fakes, _, _ = rig
    task = engine.create_task("t", [step("echo"), step("breaks"), step("echo"), step("echo")])
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "failed" and t["outcome"] == "failed"
    assert [s["status"] for s in t["steps"]] == ["succeeded", "failed", "skipped", "skipped"]
    assert fakes.runs["echo"] == 1  # steps after the failure never ran
    assert t["steps"][1]["attempts"] == 1  # failure was not marked retryable, so no retry
    report = engine.report(task["task_id"])
    assert "2. do breaks - FAILED: it broke" in report and "3. do echo - not run" in report


def test_retry_only_idempotent_and_only_when_retryable(rig):
    engine, _, fakes, _, _ = rig
    fakes.flaky_failures = 1
    t = run(engine.advance(engine.create_task("t", [step("flaky")])["task_id"]))
    assert t["status"] == "completed" and t["steps"][0]["attempts"] == 2
    fakes.runs.clear()
    fakes.flaky_failures = 99
    t = run(engine.advance(engine.create_task("t", [step("flaky")])["task_id"]))
    assert t["status"] == "failed" and t["steps"][0]["attempts"] == 3  # limit respected
    t = run(engine.advance(engine.create_task("t", [step("breaks_once_nonidem")])["task_id"]))
    assert t["status"] == "failed" and t["steps"][0]["attempts"] == 1  # never retried


def test_outcome_reflects_unverified_steps(rig):
    engine, *_ = rig
    t = run(engine.advance(engine.create_task("t", [step("echo"), step("unverified")])["task_id"]))
    assert t["status"] == "completed" and t["outcome"] == "partially_verified"
    assert "NOT verified" in engine.report(t["task_id"]) and "could not confirm" in engine.report(t["task_id"])
    t = run(engine.advance(engine.create_task("t", [step("unverified"), step("unknowable")])["task_id"]))
    assert t["outcome"] == "unverified"
    assert "not independently verifiable" in engine.report(t["task_id"])


def test_pause_and_resume_do_not_repeat_finished_steps(rig):
    engine, _, fakes, _, _ = rig
    task = engine.create_task("t", [step("echo", v=1), step("echo", v=2), step("echo", v=3)])
    tid = task["task_id"]
    fakes.on_run["echo"] = lambda: engine.pause(tid) if fakes.runs["echo"] == 1 else None
    t = run(engine.advance(tid))
    assert t["status"] == "paused" and [s["status"] for s in t["steps"]] == [
        "succeeded", "pending", "pending"]
    t = run(engine.advance(tid))
    assert t["status"] == "completed" and fakes.runs["echo"] == 3  # step 1 was not re-run


def test_cancel_while_awaiting_approval_and_between_steps(rig):
    engine, approvals, fakes, _, _ = rig
    task = engine.create_task("t", [step("danger", v=1), step("echo")])
    run(engine.advance(task["task_id"]))
    engine.cancel(task["task_id"])
    t = engine.get_task(task["task_id"])
    assert t["status"] == "cancelled" and {s["status"] for s in t["steps"]} == {"cancelled"}
    assert fakes.runs.get("danger") is None
    with pytest.raises(TaskPlanError):
        engine.cancel(task["task_id"])
    with pytest.raises(TaskPlanError):
        engine.pause(task["task_id"])

    t2 = engine.create_task("t2", [step("echo"), step("echo"), step("echo")])
    fakes.runs.clear()
    fakes.on_run["echo"] = lambda: engine.cancel(t2["task_id"]) if fakes.runs["echo"] == 1 else None
    t = run(engine.advance(t2["task_id"]))
    assert t["status"] == "cancelled" and fakes.runs["echo"] == 1
    assert [s["status"] for s in t["steps"]] == ["succeeded", "cancelled", "cancelled"]


def test_restart_recovery_never_auto_resumes_and_keeps_checkpoints(rig):
    engine, approvals, fakes, control, tmp_path = rig
    task = engine.create_task("t", [step("echo"), step("danger", v=1), step("echo")])
    tid = task["task_id"]
    t = run(engine.advance(tid))
    approvals.approve(t["steps"][1]["approval_id"])
    # simulate a crash in the middle of the dangerous step
    engine._set_task(tid, status="running")
    engine._set_step(tid, 2, status="running", attempts=1)

    fakes2 = Fakes(approvals)
    engine2 = TaskEngine(tmp_path / "t.sqlite3", approvals, fakes2.handlers(), control=control)
    assert engine2.recover() == [tid]
    t = engine2.get_task(tid)
    assert t["status"] == "interrupted" and t["steps"][0]["status"] == "succeeded"
    assert t["steps"][1]["status"] == "interrupted" and "unknown" in t["steps"][1]["note"]
    assert "outcome unknown" in engine2.report(tid)

    t = run(engine2.advance(tid))  # explicit resume alone does not re-run an unknown step
    assert fakes2.runs == {} and t["steps"][1]["status"] == "interrupted"
    assert "needs_review" in [e["kind"] for e in engine2.events(tid)]

    engine2.retry_step(tid, 2)  # owner decides to retry: it must be revalidated and re-approved
    t = engine2.get_task(tid)
    assert t["steps"][1]["approval_id"] is None and t["status"] == "paused"
    t = run(engine2.advance(tid))
    assert t["status"] == "awaiting_approval" and fakes2.runs == {}  # step 1 not re-run either
    approvals.approve(t["steps"][1]["approval_id"])
    t = run(engine2.advance(tid))
    assert t["status"] == "completed" and fakes2.runs.get("echo") == 1 and fakes2.runs["danger"] == 1


def test_interrupted_read_only_step_resumes_automatically_on_explicit_resume(rig):
    engine, approvals, fakes, control, tmp_path = rig
    task = engine.create_task("t", [step("echo"), step("echo")])
    tid = task["task_id"]
    engine._set_task(tid, status="running")
    engine._set_step(tid, 1, status="running")
    engine2 = TaskEngine(tmp_path / "t.sqlite3", approvals, Fakes(approvals).handlers(), control=control)
    engine2.recover()
    assert engine2.get_task(tid)["steps"][0]["status"] == "interrupted"
    assert run(engine2.advance(tid))["status"] == "completed"


def test_failed_task_can_retry_step_and_continue(rig):
    engine, _, fakes, _, _ = rig
    fakes.flaky_failures = 99
    task = engine.create_task("t", [step("flaky"), step("echo")])
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "failed" and t["steps"][1]["status"] == "skipped"
    fakes.flaky_failures = 0
    engine.retry_step(task["task_id"], 1)
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "completed" and t["steps"][1]["status"] == "succeeded"
    with pytest.raises(TaskPlanError):
        engine.retry_step(task["task_id"], 1)  # finished tasks cannot be reopened


def test_stop_switch_pauses_task_without_running_anything(rig):
    engine, _, fakes, control, _ = rig
    task = engine.create_task("t", [step("echo"), step("echo")])
    control.request_stop()
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "paused" and fakes.runs == {}
    assert "stopped" in [e["kind"] for e in engine.events(task["task_id"])]
    control.resume()
    assert run(engine.advance(task["task_id"]))["status"] == "completed"


def test_cannot_advance_a_running_task_twice(rig):
    engine, _, fakes, _, _ = rig
    task = engine.create_task("t", [step("echo")])
    seen = {}

    def reenter():
        try:
            run_inner = asyncio.new_event_loop()
            run_inner.run_until_complete(engine.advance(task["task_id"]))
        except TaskPlanError as exc:
            seen["err"] = str(exc)
    fakes.on_run["echo"] = reenter
    run(engine.advance(task["task_id"]))
    assert "already running" in seen["err"]


# ---------------------------------------------------------------------------------------------
# Real adapters through real handlers
# ---------------------------------------------------------------------------------------------
@pytest.fixture
def real(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    control = AutomationControl()
    ops = FileOperations(fs, control=control)
    pm = ProcessManager(approvals, control=control, sample_interval=0.05)
    terminal = TerminalExecutor(approvals, fs, os_family="linux", available_shells=("bash", "sh"),
                                environ=os.environ, control=control)
    control.on_stop(terminal.stop_all)
    handlers = build_handlers(fs=fs, file_ops=ops, processes=pm, terminal=terminal, os_family="linux")
    engine = TaskEngine(tmp_path / "t.sqlite3", approvals, handlers, control=control)
    engine.on_cancel(terminal.cancel_task)
    return engine, approvals, tmp_path / "root", terminal, control


def drive(engine, approvals, task_id, *, max_rounds=30):
    """Advance, approving every pending approval exactly as the owner would."""
    approved = []
    for _ in range(max_rounds):
        t = run(engine.advance(task_id))
        if t["status"] in {"completed", "failed", "cancelled"}:
            return t, approved
        if t["status"] == "awaiting_approval":
            for s in t["steps"]:
                if s["status"] == "awaiting_approval":
                    approvals.approve(s["approval_id"])
                    approved.append((s["idx"], s["capability"]))
        elif t["status"] in {"paused", "interrupted"}:
            return t, approved
    raise AssertionError("task did not finish")


def test_real_file_workflow_end_to_end(real):
    engine, approvals, root, _, _ = real
    (root / "src").mkdir()
    (root / "src" / "a.txt").write_text("alpha")
    (root / "src" / "dup.txt").write_text("alpha")
    steps = [
        {"capability": "filesystem.list", "args": {"root_id": "root_1", "relative_path": "src"},
         "description": "look at src"},
        {"capability": "filesystem.mkdir", "args": {"root_id": "root_1", "relative_path": "backup"}},
        {"capability": "filesystem.copy", "args": {"root_id": "root_1", "relative_path": "src/a.txt",
                                                  "destination_path": "backup/a.txt"}},
        {"capability": "filesystem.write", "args": {"root_id": "root_1", "relative_path": "notes.md",
                                                   "content": "# done\n"}},
        {"capability": "filesystem.duplicates", "args": {"root_id": "root_1", "relative_path": "."}},
        {"capability": "filesystem.read", "args": {"root_id": "root_1", "relative_path": "notes.md"}},
    ]
    task = engine.create_task("backup and note", steps)
    t, approved = drive(engine, approvals, task["task_id"])
    assert t["status"] == "completed" and t["outcome"] == "verified"
    assert [i for i, _ in approved] == [2, 3, 4]  # only the three mutating steps asked
    assert (root / "backup" / "a.txt").read_text() == "alpha"
    assert (root / "notes.md").read_text() == "# done\n"
    assert t["steps"][4]["result"]["groups"][0]["files"]  # duplicates found in the real tree
    assert t["steps"][5]["result"]["content"] == "# done\n"
    assert all(s["verified"] is True for s in t["steps"])


def test_real_delete_then_restore_via_templating(real):
    engine, approvals, root, _, _ = real
    (root / "keep.txt").write_text("precious")
    task = engine.create_task("oops", [
        {"capability": "filesystem.delete", "args": {"root_id": "root_1", "relative_path": "keep.txt"}},
        {"capability": "filesystem.restore",
         "args": {"root_id": "root_1", "recovery_id": "${steps.1.recovery_id}"}},
    ])
    plan = engine.plan_summary(task["task_id"])
    assert plan[0]["risk"] == "high" and plan[0]["needs_approval"]
    t, approved = drive(engine, approvals, task["task_id"])
    assert t["status"] == "completed" and len(approved) == 2
    assert (root / "keep.txt").read_text() == "precious"


def test_real_blocked_step_fails_safely_before_any_approval(real):
    engine, approvals, root, _, _ = real
    task = engine.create_task("bad", [
        {"capability": "filesystem.read", "args": {"root_id": "root_1", "relative_path": "../etc/passwd"}},
        {"capability": "filesystem.mkdir", "args": {"root_id": "root_1", "relative_path": "never"}}])
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "failed" and approvals.list_pending() == []
    assert not (root / "never").exists() and t["steps"][1]["status"] == "skipped"
    task = engine.create_task("blocked", [
        {"capability": "terminal.run", "args": {"command": "sudo rm -rf /", "root_id": "root_1"}}])
    t = run(engine.advance(task["task_id"]))
    assert t["status"] == "failed" and approvals.list_pending() == []


@pytest.mark.skipif(os.name != "posix", reason="uses bash")
def test_real_terminal_steps_verified_and_failure_halts(real):
    engine, approvals, root, terminal, _ = real
    task = engine.create_task("run things", [
        {"capability": "terminal.run", "args": {"command": "echo build-ok", "root_id": "root_1",
                                                "expect_output": "build-ok"}},
        {"capability": "terminal.run", "args": {"command": "echo fine", "root_id": "root_1"}},
        {"capability": "terminal.run", "args": {"command": "exit 3", "root_id": "root_1"}},
        {"capability": "filesystem.mkdir", "args": {"root_id": "root_1", "relative_path": "after"}},
    ])
    t, approved = drive(engine, approvals, task["task_id"])
    assert t["status"] == "failed"
    assert [s["status"] for s in t["steps"]] == ["succeeded", "succeeded", "failed", "skipped"]
    assert t["steps"][0]["verified"] is True            # expected output actually seen
    assert t["steps"][1]["verified"] is None            # exit 0 alone is not goal verification
    assert "exit code 3" in t["steps"][2]["error"] and not (root / "after").exists()
    assert t["steps"][0]["result"]["stdout"].strip() == "build-ok"


@pytest.mark.skipif(os.name != "posix", reason="uses bash")
def test_real_dev_server_ready_detection(real):
    engine, approvals, root, terminal, _ = real
    script = "import time; print('listening on 8000', flush=True); time.sleep(60)"
    ok = engine.create_task("start server", [{"capability": "terminal.run", "args": {
        "command": f"python3 -u -c \"{script}\"", "root_id": "root_1",
        "ready_pattern": "listening on", "ready_timeout_seconds": 10}}])
    t, _ = drive(engine, approvals, ok["task_id"])
    data = t["steps"][0]["result"]
    assert t["status"] == "completed" and data["ready"] and data["still_running"]
    assert terminal.get(data["execution_id"]).status == "running"
    terminal.cancel(data["execution_id"])
    terminal.wait(data["execution_id"], 5)

    never = engine.create_task("never ready", [{"capability": "terminal.run", "args": {
        "command": "sleep 30", "root_id": "root_1", "ready_pattern": "ready!",
        "ready_timeout_seconds": 0.5}}])
    t, _ = drive(engine, approvals, never["task_id"])
    assert t["status"] == "failed" and "readiness" in t["steps"][0]["error"]
    ex = terminal.get(t["steps"][0]["result"]["execution_id"])
    terminal.wait(ex.execution_id, 5)
    assert ex.status == "cancelled"  # a server that never became ready is not left running


@pytest.mark.skipif(os.name != "posix", reason="uses bash")
def test_real_cancel_stops_running_command(real):
    engine, approvals, root, terminal, _ = real
    task = engine.create_task("long", [
        {"capability": "terminal.run", "args": {"command": "sleep 30", "root_id": "root_1"}},
        {"capability": "filesystem.mkdir", "args": {"root_id": "root_1", "relative_path": "never"}}])
    t = run(engine.advance(task["task_id"]))
    approvals.approve(t["steps"][0]["approval_id"])

    async def scenario():
        async def canceller():
            await asyncio.sleep(0.6)
            engine.cancel(task["task_id"])
        return (await asyncio.gather(engine.advance(task["task_id"]), canceller()))[0]
    final = run(scenario())
    assert final["status"] == "cancelled" and final["steps"][0]["status"] == "cancelled"
    assert final["steps"][1]["status"] == "cancelled" and not (root / "never").exists()


@pytest.mark.skipif(os.name != "posix", reason="uses bash")
def test_real_process_step_requires_approval_and_verifies(real):
    engine, approvals, root, _, _ = real
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        task = engine.create_task("stop it", [
            {"capability": "process.info", "args": {"pid": child.pid}},
            {"capability": "process.terminate", "args": {"pid": child.pid}}])
        t = run(engine.advance(task["task_id"]))
        assert t["status"] == "awaiting_approval" and child.poll() is None
        t, approved = drive(engine, approvals, task["task_id"])
        assert t["status"] == "completed" and t["steps"][1]["verified"] is True
        child.wait(timeout=5)
    finally:
        child.kill()
        child.wait()


def test_handlers_registered_only_where_supported():
    approvals = None
    linux = build_handlers(os_family="linux")
    assert linux == {}
    fake = object()
    win = build_handlers(desktop=fake, desktop_adapter=fake, os_family="windows")
    lin = build_handlers(desktop=fake, desktop_adapter=fake, os_family="linux")
    assert "desktop.click" in win and "desktop.observe" in win
    assert not [k for k in lin if k.startswith("desktop.")] and approvals is None


# ---------------------------------------------------------------------------------------------
# Browser step handlers against a real browser (skipped without Chromium)
# ---------------------------------------------------------------------------------------------
EXE = os.environ.get("MIA_TEST_BROWSER") or None


class Page(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        body = {"/": "<html><head><title>Home</title></head><body><h1>Home</h1>"
                     "<a href='/next'>Continue here</a></body></html>",
                "/next": "<html><head><title>Next</title></head><body><h1>Arrived</h1>"
                         "<table><tr><th>k</th></tr><tr><td>v1</td></tr></table></body></html>"}.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.mark.skipif(not (EXE and os.path.exists(EXE)), reason="set MIA_TEST_BROWSER to a Chromium")
def test_real_browser_task_with_find_and_approved_click(tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    port = httpd.server_address[1]

    def policy(url):
        p = urlsplit(url)
        if p.hostname == "127.0.0.1" and p.port == port:
            return url
        raise BrowserAccessError("blocked")

    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    control = AutomationControl()

    async def scenario():
        browser = BrowserController(executable_path=EXE, launch_args=("--no-sandbox", "--disable-gpu"),
                                    url_policy=policy)
        actions = BrowserActions(browser, approvals, None, control=control)
        engine = TaskEngine(tmp_path / "t.sqlite3", approvals,
                            build_handlers(browser=browser, browser_actions=actions),
                            control=control)
        try:
            task = engine.create_task("browse", [
                {"capability": "browser.navigate", "args": {"url": base + "/"}},
                {"capability": "browser.find", "args": {"query": "Continue"}},
                {"capability": "browser.click_ref", "args": {"ref": "${steps.2.items.0.ref}"}},
                {"capability": "browser.extract", "args": {}},
            ])
            t = await engine.advance(task["task_id"])
            assert t["status"] == "awaiting_approval" and t["steps"][2]["status"] == "awaiting_approval"
            approvals.approve(t["steps"][2]["approval_id"])
            t = await engine.advance(task["task_id"])
            assert t["status"] == "completed", engine.report(task["task_id"])
            assert t["steps"][2]["verified"] is True
            assert t["steps"][3]["result"]["tables"][0]["rows"] == [["v1"]]
        finally:
            await browser.close()
            httpd.shutdown()
    run(scenario())


# ---------------------------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------------------------
def test_task_api_flow(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    (tmp_path / "root" / "a.txt").write_text("hi")
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"),
                                   filesystem=fs, approvals=approvals))
    caps = client.get("/api/computer/tasks/capabilities").json()["items"]
    assert "filesystem.copy" in caps and "terminal.run" in caps

    bad = client.post("/api/computer/tasks", json={"title": "x", "steps": [
        {"capability": "format.disk", "args": {}}]})
    assert bad.status_code == 422
    assert client.post("/api/computer/tasks", json={"title": "x", "steps": [
        {"capability": "filesystem.list", "args": {"root_id": "root_1"}, "grant": True}]}).status_code == 422

    body = {"title": "copy file", "steps": [
        {"capability": "filesystem.list", "args": {"root_id": "root_1"}, "description": "look"},
        {"capability": "filesystem.copy", "args": {"root_id": "root_1", "relative_path": "a.txt",
                                                  "destination_path": "b.txt"}}]}
    created = client.post("/api/computer/tasks", json=body)
    assert created.status_code == 201
    tid = created.json()["task"]["task_id"]
    assert [p["needs_approval"] for p in created.json()["plan"]] == [False, True]

    first = client.post(f"/api/computer/tasks/{tid}/run").json()
    assert first["status"] == "awaiting_approval" and not (tmp_path / "root" / "b.txt").exists()
    aid = first["steps"][1]["approval_id"]
    pending = client.get("/api/computer/approvals").json()["items"]
    assert [p["approval_id"] for p in pending] == [aid]
    client.post(f"/api/computer/approvals/{aid}/approve")
    done = client.post(f"/api/computer/tasks/{tid}/run").json()
    assert done["status"] == "completed" and (tmp_path / "root" / "b.txt").read_text() == "hi"
    rep = client.get(f"/api/computer/tasks/{tid}/report").json()
    assert rep["outcome"] == "verified" and "copy" in rep["report"]
    assert client.get(f"/api/computer/tasks/{tid}/events").json()["items"]
    assert client.get("/api/computer/tasks").json()["items"][0]["task_id"] == tid
    assert client.get("/api/computer/tasks/nope").status_code == 404
    assert client.post("/api/computer/tasks/nope/run").status_code == 404
    assert client.post(f"/api/computer/tasks/{tid}/cancel").status_code == 422  # already finished

    second = client.post("/api/computer/tasks", json={**body, "title": "again"}).json()["task"]["task_id"]
    assert client.post(f"/api/computer/tasks/{second}/cancel").json()["status"] == "cancel_requested"
    assert client.get(f"/api/computer/tasks/{second}").json()["status"] == "cancelled"
    fresh = {**body, "title": "stopped", "steps": [body["steps"][0], {
        "capability": "filesystem.copy",
        "args": {"root_id": "root_1", "relative_path": "a.txt", "destination_path": "c.txt"}}]}
    third = client.post("/api/computer/tasks", json=fresh).json()["task"]["task_id"]
    client.post("/api/computer/stop-all")
    paused = client.post(f"/api/computer/tasks/{third}/run").json()
    assert paused["status"] == "paused"
    client.post("/api/computer/resume")
    assert client.post(f"/api/computer/tasks/{third}/run").json()["status"] == "awaiting_approval"


def test_owner_permission_grant_covers_medium_task_without_extra_approval(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    registry = default_registry()
    target = tmp_path / "workspace" / "allowed.txt"
    target.parent.mkdir()

    policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(target.parent),
        kind="session",
    )
    executed = []

    def request(args):
        return approvals.request(
            action_id="filesystem.write",
            payload={"path": args["path"]},
            summary="write file",
        )["approval_id"]

    def run_step(args, approval_id):
        approvals.consume(
            approval_id=approval_id,
            action_id="filesystem.write",
            payload={"path": args["path"]},
        )
        executed.append(args["path"])
        return Outcome(True, True, {"path": args["path"]})

    handlers = {
        "filesystem.write": StepHandler(
            "filesystem.write",
            lambda args: Inspection("write target", "medium", True),
            run_step,
            request_approval=request,
            idempotent=False,
            required=("path",),
        )
    }
    engine = TaskEngine(
        tmp_path / "tasks.sqlite3",
        approvals,
        handlers,
        permission_policy=policy,
        capability_registry=registry,
        permission_path=lambda _cap, args: args["path"],
    )
    task = engine.create_task(
        "Write approved file",
        [{"capability": "filesystem.write", "args": {"path": str(target)}}],
    )
    assert engine.plan_summary(task["task_id"])[0]["needs_approval"] is False
    completed = run(engine.advance(task["task_id"]))
    assert completed["status"] == "completed"
    assert executed == [str(target)]
    assert approvals.list_pending() == []
    assert any(event["event"] == "authorize" and event["outcome"] == "allowed"
               for event in policy.history())


def test_high_risk_task_still_requires_per_action_approval_despite_grant(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    registry = default_registry()
    target = tmp_path / "workspace" / "delete.txt"
    target.parent.mkdir()

    policy.grant(
        granted_by="owner",
        capabilities=["filesystem.delete"],
        level=PermissionLevel.WRITE,
        directory=str(target.parent),
        kind="session",
    )
    handler = StepHandler(
        "filesystem.delete",
        lambda args: Inspection("delete target", "high", True),
        lambda args, approval_id: Outcome(True, True, {"approval": approval_id}),
        request_approval=lambda args: approvals.request(
            action_id="filesystem.delete",
            payload={"path": args["path"]},
            summary="delete target",
        )["approval_id"],
        idempotent=False,
        required=("path",),
    )
    engine = TaskEngine(
        tmp_path / "tasks.sqlite3",
        approvals,
        {"filesystem.delete": handler},
        permission_policy=policy,
        capability_registry=registry,
        permission_path=lambda _cap, args: args["path"],
    )
    task = engine.create_task(
        "Delete protected file",
        [{"capability": "filesystem.delete", "args": {"path": str(target)}}],
    )
    assert engine.plan_summary(task["task_id"])[0]["needs_approval"] is True
    waiting = run(engine.advance(task["task_id"]))
    assert waiting["status"] == "awaiting_approval"
    assert waiting["steps"][0]["status"] == "awaiting_approval"
    assert len(approvals.list_pending()) == 1


def test_directory_scoped_grant_does_not_cover_an_outside_path(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    registry = default_registry()
    allowed_dir = tmp_path / "workspace" / "allowed"
    outside = tmp_path / "workspace" / "outside" / "file.txt"
    allowed_dir.mkdir(parents=True)
    outside.parent.mkdir(parents=True)

    policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(allowed_dir),
        kind="session",
    )
    handler = StepHandler(
        "filesystem.write",
        lambda args: Inspection("write target", "medium", True),
        lambda args, _approval: Outcome(True, True),
        request_approval=lambda args: approvals.request(
            action_id="filesystem.write", payload={"path": args["path"]}, summary="write"
        )["approval_id"],
        idempotent=False,
        required=("path",),
    )
    engine = TaskEngine(
        tmp_path / "tasks.sqlite3",
        approvals,
        {"filesystem.write": handler},
        permission_policy=policy,
        capability_registry=registry,
        permission_path=lambda _cap, args: args["path"],
    )
    task = engine.create_task(
        "Write outside grant",
        [{"capability": "filesystem.write", "args": {"path": str(outside)}}],
    )
    assert engine.plan_summary(task["task_id"])[0]["needs_approval"] is True
    waiting = run(engine.advance(task["task_id"]))
    assert waiting["status"] == "awaiting_approval"


def test_non_idempotent_task_without_permission_spec_fails_closed(tmp_path):
    approvals = ApprovalStore(tmp_path / "unknown-approvals.sqlite3")
    fake = Fakes(approvals)
    policy = PermissionPolicy(tmp_path / "unknown-permissions.sqlite3")
    engine = TaskEngine(
        tmp_path / "unknown-tasks.sqlite3",
        approvals,
        fake.handlers(),
        permission_policy=policy,
        capability_registry=default_registry(),
    )
    task = engine.create_task("Unregistered side effect", [step("danger", v=42)])
    plan = engine.plan_summary(task["task_id"])
    assert plan[0]["needs_approval"] is True
    assert "PERMISSION DENIED" in plan[0]["summary"]

    result = run(engine.advance(task["task_id"]))
    assert result["status"] == "failed"
    assert fake.runs.get("danger") is None
    assert any(event["kind"] == "permission_denied" for event in engine.events(task["task_id"]))


def test_unregistered_read_capability_fails_closed_with_shared_policy(tmp_path):
    approvals = ApprovalStore(tmp_path / "unknown-capability-approvals.sqlite3")
    policy = PermissionPolicy(tmp_path / "unknown-capability-permissions.sqlite3")
    handler = StepHandler(
        "unregistered.read",
        lambda _args: Inspection("read unknown resource", "low"),
        lambda _args, _approval: Outcome(True, True, {"ok": True}),
        idempotent=True,
    )
    engine = TaskEngine(
        tmp_path / "unknown-capability-tasks.sqlite3",
        approvals,
        {"unregistered.read": handler},
        permission_policy=policy,
        capability_registry=default_registry(),
    )
    decision = engine._permission_decision(
        "unregistered.read", {}, Inspection("read unknown resource", "low")
    )
    assert decision is not None
    assert decision.allowed is False
    assert "registered owner permission specification" in decision.reason
