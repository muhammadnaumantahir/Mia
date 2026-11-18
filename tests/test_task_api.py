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
