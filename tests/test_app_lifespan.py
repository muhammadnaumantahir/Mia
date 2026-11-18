from fastapi.testclient import TestClient

from mia.agent.tasks import TaskStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_startup_hook_recovers_interrupted_tasks_as_failed(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = tasks.create_task(
        title="Interrupted task",
        goal="Was running when the process stopped.",
        source="interactive",
        metadata={},
    )
    tasks.transition(task_id, "approved", reason="Owner approved the task")
    tasks.transition(task_id, "running", reason="Execution started")

    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        task_store=tasks,
    )
    assert tasks.get_task(task_id)["status"] == "running"

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "mia"}
        assert tasks.get_task(task_id)["status"] == "failed"


def test_app_starts_and_stops_cleanly_with_lifespan(tmp_path):
    app = create_app(store=ObservatoryStore(tmp_path / "observatory.sqlite3"))

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
