from fastapi.testclient import TestClient

from mia.gateway.service import CompletionResult
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeGateway:
    async def discover_models(self):
        return [object()]

    async def complete(self, request, candidates, policy):
        return CompletionResult(
            provider_id="local",
            model_id="fake",
            content='{"tasks":[]}',
            attempted_candidates=("local/fake",),
            data_local=True,
        )


def test_planning_api_returns_empty_safe_plan_without_approvals(tmp_path):
    app = create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"),
        gateway=FakeGateway(),
    )
    response = TestClient(app).post(
        "/api/planning/propose",
        json={"objective": "Review my project health."},
    )
    assert response.status_code == 201
    assert response.json() == {"items": [], "approved_automatically": False}


def test_planning_api_validates_request_bounds(tmp_path):
    app = create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"),
        gateway=FakeGateway(),
    )
    response = TestClient(app).post(
        "/api/planning/propose",
        json={"objective": "Plan", "max_tasks": 11},
    )
    assert response.status_code == 422
