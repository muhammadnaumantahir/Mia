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



class ProposalGateway:
    async def discover_models(self):
        return [object()]

    async def complete(self, request, candidates, policy):
        return CompletionResult(
            provider_id="local",
            model_id="fake",
            content=(
                '{"tasks":[{"title":"Inspect test coverage",'
                '"goal":"Review test gaps and write a findings report.",'
                '"risk_level":"low","priority":55,'
                '"evidence_summary":"The user requested a project health review.","execution":null}]}'
            ),
            attempted_candidates=("local/fake",),
            data_local=True,
        )


def test_planning_api_persists_proposal_but_never_approves_it(tmp_path):
    from mia.agent.tasks import TaskStore

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"),
        gateway=ProposalGateway(),
        task_store=tasks,
    )
    response = TestClient(app).post(
        "/api/planning/propose",
        json={
            "objective": "Review test coverage.",
            "evidence": "The repository has a test suite.",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["approved_automatically"] is False
    assert len(body["items"]) == 1
    proposal = body["items"][0]
    assert proposal["status"] == "proposed"
    assert proposal["requires_governance_review"] is True
    assert tasks.get_task(proposal["task_id"])["status"] == "proposed"
