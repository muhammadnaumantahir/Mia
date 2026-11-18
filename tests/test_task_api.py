import pytest

from fastapi.testclient import TestClient

from mia.agent.tasks import TaskStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_task_api_requires_approval_and_records_transitions(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
        )
    )

    autonomy = client.get("/api/autonomy/status").json()
    assert autonomy["enabled"] is False
    assert autonomy["automatic_dispatch"] is False

    created = client.post(
        "/api/tasks",
        json={
            "title": "Review project notes",
            "goal": "Search the configured research folder for the latest thesis notes.",
            "source": "interactive",
            "priority": 60,
        },
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]
    assert created.json()["status"] == "proposed"

    task = client.get(f"/api/tasks/{task_id}").json()
    assert task["task"]["status"] == "proposed"
    assert task["events"][0]["to_status"] == "proposed"

    approved = client.post(
        f"/api/tasks/{task_id}/approve",
        json={"reason": "Approved for a read-only review."},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    rejected_after_approval = client.post(
        f"/api/tasks/{task_id}/reject",
        json={"reason": "Too late to reject approved task."},
    )
    assert rejected_after_approval.status_code == 409

    cancelled = client.post(
        f"/api/tasks/{task_id}/cancel",
        json={"reason": "Owner cancelled the task."},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_task_api_validates_source_and_priority(tmp_path):
    client = TestClient(
        create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3"))
    )
    response = client.post(
        "/api/tasks",
        json={
            "title": "Invalid task",
            "goal": "This has an invalid source.",
            "source": "unknown_source",
            "priority": 101,
        },
    )
    assert response.status_code == 422



class FakePsutil:
    def cpu_percent(self, interval):
        return 10

    def virtual_memory(self):
        from types import SimpleNamespace
        return SimpleNamespace(percent=20, available=800, total=1000)

    def process_iter(self, attrs):
        return iter([])


def test_manual_task_execution_requires_approval_and_runs_read_only_tool(tmp_path):
    from mia.agent.resources import ResourceMonitor

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
            resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        )
    )
    created = client.post(
        "/api/tasks",
        json={
            "title": "Check local resource snapshot",
            "goal": "Read the current CPU and memory usage.",
            "source": "interactive",
            "metadata": {
                "execution": {
                    "tool_name": "system.read_resources",
                    "arguments": {},
                }
            },
        },
    )
    assert created.status_code == 201
    task_id = created.json()["task_id"]

    before_approval = client.post(f"/api/tasks/{task_id}/execute")
    assert before_approval.status_code == 409
    assert tasks.get_task(task_id)["status"] == "proposed"

    approved = client.post(
        f"/api/tasks/{task_id}/approve",
        json={"reason": "Approved this read-only system check."},
    )
    assert approved.status_code == 200

    executed = client.post(f"/api/tasks/{task_id}/execute")
    assert executed.status_code == 200
    body = executed.json()
    assert body["status"] == "completed"
    assert body["task"]["status"] == "completed"
    assert body["outcome"]["tool_name"] == "system.read_resources"
    assert body["outcome"]["result"]["cpu_percent"] == 10
    assert [event["to_status"] for event in tasks.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "completed",
    ]


def test_manual_task_execution_rejects_preview_tools_without_claiming_task(tmp_path):
    from mia.agent.resources import ResourceMonitor

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
            resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        )
    )
    created = client.post(
        "/api/tasks",
        json={
            "title": "Prepare a file write",
            "goal": "Preview a write but do not execute it.",
            "source": "interactive",
            "metadata": {
                "execution": {
                    "tool_name": "filesystem.preview_write",
                    "arguments": {
                        "root_id": "workspace",
                        "relative_path": "notes.txt",
                        "content": "not executed",
                    },
                }
            },
        },
    )
    task_id = created.json()["task_id"]
    client.post(
        f"/api/tasks/{task_id}/approve",
        json={"reason": "Approved for task validation only."},
    )

    response = client.post(f"/api/tasks/{task_id}/execute")
    assert response.status_code == 422
    assert "read-only" in response.json()["detail"]
    assert tasks.get_task(task_id)["status"] == "approved"


def test_manual_task_execution_respects_global_emergency_stop(tmp_path, monkeypatch):
    from mia.agent.resources import ResourceMonitor

    monkeypatch.setenv("MIA_EMERGENCY_STOP", "true")
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
            resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        )
    )
    created = client.post(
        "/api/tasks",
        json={
            "title": "Check local resources",
            "goal": "Read CPU and RAM values.",
            "source": "interactive",
            "metadata": {
                "execution": {
                    "tool_name": "system.read_resources",
                    "arguments": {},
                }
            },
        },
    )
    task_id = created.json()["task_id"]
    client.post(
        f"/api/tasks/{task_id}/approve",
        json={"reason": "Approved before enabling emergency stop."},
    )

    response = client.post(f"/api/tasks/{task_id}/execute")
    assert response.status_code == 503
    assert response.json()["detail"] == "Emergency stop is active"
    assert tasks.get_task(task_id)["status"] == "approved"


@pytest.mark.asyncio
async def test_cancelled_execution_request_marks_running_task_cancelled(tmp_path):
    import asyncio
    import httpx

    from mia.agent.resources import ResourceMonitor

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        task_store=tasks,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    )

    class BlockingExecutor:
        def validate(self, task):
            assert task["status"] == "approved"

        async def execute(self, task):
            await asyncio.Event().wait()

    app.state.task_tool_executor = BlockingExecutor()
    task_id = tasks.create_task(
        title="Cancelable read task",
        goal="Wait for a cancellation signal.",
        source="interactive",
        metadata={
            "execution": {
                "tool_name": "system.read_resources",
                "arguments": {},
            }
        },
    )
    tasks.transition(task_id, "approved", reason="Owner approved the task")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mia.test") as client:
        request = asyncio.create_task(client.post(f"/api/tasks/{task_id}/execute"))
        for _ in range(100):
            if tasks.get_task(task_id)["status"] == "running":
                break
            await asyncio.sleep(0.01)
        assert tasks.get_task(task_id)["status"] == "running"
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request

    assert tasks.get_task(task_id)["status"] == "cancelled"
    assert [event["to_status"] for event in tasks.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
        "cancelled",
    ]


def test_manual_task_execution_rejects_invalid_arguments_before_claiming(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
        )
    )
    created = client.post(
        "/api/tasks",
        json={
            "title": "Malformed resource check",
            "goal": "Attempt a resource check with unexpected arguments.",
            "source": "interactive",
            "metadata": {
                "execution": {
                    "tool_name": "system.read_resources",
                    "arguments": {"unexpected": True},
                }
            },
        },
    )
    task_id = created.json()["task_id"]
    client.post(
        f"/api/tasks/{task_id}/approve",
        json={"reason": "Approve only to exercise pre-claim validation."},
    )

    response = client.post(f"/api/tasks/{task_id}/execute")
    assert response.status_code == 422
    assert "Unexpected argument" in response.json()["detail"]
    assert tasks.get_task(task_id)["status"] == "approved"
    assert [event["to_status"] for event in tasks.list_events(task_id)] == [
        "proposed",
        "approved",
    ]


def test_background_autonomy_dispatch_is_opt_in_and_read_only(tmp_path, monkeypatch):
    import time

    from mia.agent.resources import ResourceMonitor

    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "true")
    monkeypatch.setenv("MIA_EMERGENCY_STOP", "false")
    tasks = TaskStore(tmp_path / "background-tasks.sqlite3")

    invalid_id = tasks.create_task(
        title="Keep preview task queued",
        goal="This preview must never be dispatched automatically.",
        source="interactive",
        priority=90,
        metadata={
            "execution": {
                "tool_name": "filesystem.preview_write",
                "arguments": {
                    "root_id": "workspace",
                    "relative_path": "not-created.txt",
                    "content": "must not execute",
                },
            }
        },
    )
    valid_id = tasks.create_task(
        title="Automatic system inspection",
        goal="Read the CPU and memory usage only.",
        source="interactive",
        priority=50,
        metadata={
            "execution": {
                "tool_name": "system.read_resources",
                "arguments": {},
            }
        },
    )
    tasks.transition(invalid_id, "approved", reason="Owner approved preview for manual review")
    tasks.transition(valid_id, "approved", reason="Owner approved read-only system inspection")

    app = create_app(
        store=ObservatoryStore(tmp_path / "background-observatory.sqlite3"),
        task_store=tasks,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    )
    with TestClient(app) as client:
        status = client.get("/api/autonomy/status").json()
        assert status["enabled"] is True
        assert status["automatic_dispatch"] is True
        assert status["automatic_tools"] == [
            "applications.list",
            "system.list_processes",
            "system.read_resources",
        ]

        for _ in range(100):
            if tasks.get_task(valid_id)["status"] == "completed":
                break
            time.sleep(0.02)

        assert tasks.get_task(valid_id)["status"] == "completed"
        assert tasks.get_task(invalid_id)["status"] == "approved"
        assert client.post("/api/autonomy/pause").status_code == 200
        for _ in range(100):
            if not client.get("/api/autonomy/status").json()["automatic_dispatch"]:
                break
            time.sleep(0.01)
        assert client.get("/api/autonomy/status").json()["automatic_dispatch"] is False

    assert any(
        event["event_type"] == "background_task_completed"
        for event in app.state.observatory.list_events(limit=30)
    )


def test_application_startup_recovers_interrupted_tasks_without_auto_retry(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "false")
    tasks = TaskStore(tmp_path / "recovery-tasks.sqlite3")
    task_id = tasks.create_task(
        title="Interrupted before restart",
        goal="Read a bounded local resource snapshot.",
        source="interactive",
        metadata={
            "execution": {
                "tool_name": "system.read_resources",
                "arguments": {},
            }
        },
    )
    tasks.transition(task_id, "approved", reason="Owner approved before simulated restart")
    tasks.claim_next()

    app = create_app(
        store=ObservatoryStore(tmp_path / "recovery-observatory.sqlite3"),
        task_store=tasks,
    )
    with TestClient(app) as client:
        task = client.get(f"/api/tasks/{task_id}").json()["task"]
        assert task["status"] == "failed"
        assert tasks.list_events(task_id)[-1]["actor"] == "startup-recovery"

    assert tasks.get_task(task_id)["status"] == "failed"
    assert any(
        event["event_type"] == "interrupted_tasks_recovered"
        for event in app.state.observatory.list_events(limit=20)
    )


def test_autonomy_pause_survives_application_restart_until_explicit_resume(
    tmp_path,
    monkeypatch,
):
    from mia.agent.resources import ResourceMonitor

    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "true")
    monkeypatch.setenv("MIA_EMERGENCY_STOP", "false")
    tasks = TaskStore(tmp_path / "persistent-pause-tasks.sqlite3")
    monitor = ResourceMonitor(psutil_module=FakePsutil())

    first_app = create_app(
        store=ObservatoryStore(tmp_path / "persistent-pause-observatory-1.sqlite3"),
        task_store=tasks,
        resource_monitor=monitor,
    )
    with TestClient(first_app) as first_client:
        assert first_client.get("/api/autonomy/status").json()["automatic_dispatch"] is True
        paused = first_client.post("/api/autonomy/pause")
        assert paused.status_code == 200
        assert paused.json()["paused"] is True

    second_app = create_app(
        store=ObservatoryStore(tmp_path / "persistent-pause-observatory-2.sqlite3"),
        task_store=tasks,
        resource_monitor=monitor,
    )
    with TestClient(second_app) as second_client:
        status = second_client.get("/api/autonomy/status").json()
        assert status["enabled"] is True
        assert status["paused"] is True
        assert status["automatic_dispatch"] is False

        resumed = second_client.post("/api/autonomy/resume")
        assert resumed.status_code == 200
        assert resumed.json()["paused"] is False
        assert resumed.json()["automatic_dispatch"] is True

        paused_again = second_client.post("/api/autonomy/pause")
        assert paused_again.status_code == 200
        assert paused_again.json()["paused"] is True

    event_types = [
        event["event_type"]
        for event in second_app.state.observatory.list_events(limit=20)
    ]
    assert "background_dispatch_resumed" in event_types
    assert "background_dispatch_paused" in event_types


@pytest.mark.parametrize(
    ("enabled", "emergency_stop"),
    [(False, False), (True, True)],
)
def test_autonomy_resume_cannot_clear_pause_when_policy_blocks_dispatch(
    tmp_path,
    monkeypatch,
    enabled,
    emergency_stop,
):
    monkeypatch.setenv("MIA_AUTONOMY_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("MIA_EMERGENCY_STOP", "true" if emergency_stop else "false")
    tasks = TaskStore(tmp_path / f"blocked-resume-{enabled}-{emergency_stop}.sqlite3")
    tasks.set_autonomy_paused(True)
    app = create_app(
        store=ObservatoryStore(tmp_path / f"blocked-resume-obs-{enabled}-{emergency_stop}.sqlite3"),
        task_store=tasks,
    )

    with TestClient(app) as client:
        response = client.post("/api/autonomy/resume")
        assert response.status_code == 409
        assert tasks.is_autonomy_paused() is True
        state = client.get("/api/autonomy/status").json()
        assert state["paused"] is True
        assert state["automatic_dispatch"] is False
