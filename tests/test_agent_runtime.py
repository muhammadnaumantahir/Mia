from types import SimpleNamespace

import pytest

from mia.agent.resources import ResourceMonitor
from mia.agent.runtime import AutonomyRuntime
from mia.agent.tasks import AutonomyPolicy, TaskStore


class FakePsutil:
    def cpu_percent(self, interval):
        return 10
    def virtual_memory(self):
        return SimpleNamespace(percent=20, available=800, total=1000)
    def process_iter(self, attrs):
        return iter([])


def test_runtime_requires_explicit_enable_before_start(tmp_path):
    runtime = AutonomyRuntime(tasks=TaskStore(tmp_path / "tasks.sqlite3"),
        monitor=ResourceMonitor(psutil_module=FakePsutil()),
        policy=AutonomyPolicy(enabled=False), executor=lambda task: {"ok": True})
    with pytest.raises(RuntimeError, match="explicitly enabled"):
        runtime.start()
    assert not runtime.running


def test_runtime_cycle_dispatches_only_approved_task(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = tasks.create_task(title="Bounded internal check",
        goal="Run a harmless internal check.", source="internal_test")
    tasks.transition(task_id, "approved", reason="Owner approved")
    runtime = AutonomyRuntime(tasks=tasks, monitor=ResourceMonitor(psutil_module=FakePsutil()),
        policy=AutonomyPolicy(enabled=True), executor=lambda task: {"check": "ok"})
    result = runtime.run_cycle()
    assert result["status"] == "completed"
    assert tasks.get_task(task_id)["status"] == "completed"


def test_runtime_emergency_stop_prevents_task_claim(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = tasks.create_task(title="Wait safely",
        goal="Do not run while emergency stop is active.", source="internal_test")
    tasks.transition(task_id, "approved", reason="Owner approved")
    runtime = AutonomyRuntime(tasks=tasks, monitor=ResourceMonitor(psutil_module=FakePsutil()),
        policy=AutonomyPolicy(enabled=True, emergency_stop=True),
        executor=lambda task: {"unexpected": True})
    assert runtime.run_cycle() == {"status": "paused", "reason": "emergency_stop"}
    assert tasks.get_task(task_id)["status"] == "approved"


def test_runtime_resource_failure_pauses_without_claiming(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = tasks.create_task(title="Resource unavailable",
        goal="Keep approved task queued if telemetry fails.", source="internal_test")
    tasks.transition(task_id, "approved", reason="Owner approved")
    class BrokenMonitor:
        def snapshot(self):
            raise RuntimeError("telemetry unavailable")
    runtime = AutonomyRuntime(tasks=tasks, monitor=BrokenMonitor(),
        policy=AutonomyPolicy(enabled=True), executor=lambda task: {"unexpected": True})
    result = runtime.run_cycle()
    assert result["status"] == "paused"
    assert result["reason"] == "resource_or_policy_error"
    assert tasks.get_task(task_id)["status"] == "approved"

    
def test_observability_callback_failure_does_not_fail_completed_task(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = tasks.create_task(
        title="Complete despite telemetry issue",
        goal="Finish the approved bounded task.",
        source="internal_test",
    )
    tasks.transition(task_id, "approved", reason="Owner approved")
    runtime = AutonomyRuntime(
        tasks=tasks,
        monitor=ResourceMonitor(psutil_module=FakePsutil()),
        policy=AutonomyPolicy(enabled=True),
        executor=lambda task: {"ok": True},
        on_cycle=lambda result: (_ for _ in ()).throw(RuntimeError("storage offline")),
    )

    result = runtime.run_cycle()
    assert result["status"] == "completed"
    assert result["observability_error"] == "RuntimeError"
    assert tasks.get_task(task_id)["status"] == "completed"
