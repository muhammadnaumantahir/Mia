from fastapi.testclient import TestClient

from mia.agent.tasks import TaskStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_discovery_api_records_proposals_without_approving(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            task_store=tasks,
        )
    )
    response = client.post(
        "/api/agent/discovery/proposals",
        json={
            "findings": [
                {
                    "source": "internal_test",
                    "evidence_id": "test-suite:failure-42",
                    "title": "Investigate failed internal test",
                    "goal": "Reproduce and diagnose the failing test case.",
                    "evidence_summary": "One deterministic test failed in the latest run.",
                    "risk_level": "low",
                }
            ]
        },
    )
    assert response.status_code == 201
    result = response.json()
    assert result["approved_automatically"] is False
    task = tasks.get_task(result["items"][0]["task_id"])
    assert task["status"] == "proposed"
