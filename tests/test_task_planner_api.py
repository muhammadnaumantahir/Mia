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


class ExecutableProposalGateway:
    def __init__(self):
        self.prompt = ""

    async def discover_models(self):
        return [object()]

    async def complete(self, request, candidates, policy):
        self.prompt = request.prompt
        return CompletionResult(
            provider_id="local",
            model_id="fake",
            content=(
                '{"tasks":[{"title":"Check local resources",'
                '"goal":"Read current CPU and memory usage.",'
                '"risk_level":"low","priority":60,'
                '"evidence_summary":"The owner asked for a local health check.",'
                '"execution":{"tool_name":"system.read_resources","arguments":{}}}]}'
            ),
            attempted_candidates=("local/fake",),
            data_local=True,
        )


def test_planner_api_validates_registry_tool_and_keeps_proposal_unapproved(tmp_path):
    from mia.agent.tasks import TaskStore

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    gateway = ExecutableProposalGateway()
    app = create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"),
        gateway=gateway,
        task_store=tasks,
    )

    response = TestClient(app).post(
        "/api/planning/propose",
        json={"objective": "Check local resource pressure."},
    )

    assert response.status_code == 201
    proposal = response.json()["items"][0]
    saved = tasks.get_task(proposal["task_id"])
    assert saved["status"] == "proposed"
    assert proposal["status"] == "proposed"
    assert saved["metadata"]["execution"] == {
        "tool_name": "system.read_resources",
        "arguments": {},
    }
    assert "system.read_resources" in gateway.prompt
    assert "filesystem.preview_write" not in gateway.prompt


class MultiStepProposalGateway:
    async def discover_models(self):
        return [object()]

    async def complete(self, request, candidates, policy):
        return CompletionResult(
            provider_id="local",
            model_id="fake",
            content=(
                '{"tasks":[{"title":"Inspect system health",'
                '"goal":"Read resource pressure then a bounded process inventory.",'
                '"risk_level":"low","priority":65,'
                '"evidence_summary":"The owner requested a read-only local health check.",'
                '"execution":{"steps":['
                '{"tool_name":"system.read_resources","arguments":{}},'
                '{"tool_name":"system.list_processes","arguments":{"limit":5}}'
                ']}}]}'
            ),
            attempted_candidates=("local/fake",),
            data_local=True,
        )


def test_planner_api_persists_bounded_multistep_read_only_contract(tmp_path):
    from mia.agent.tasks import TaskStore

    tasks = TaskStore(tmp_path / "multi-plan-tasks.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "multi-plan-observatory.sqlite3"),
        gateway=MultiStepProposalGateway(),
        task_store=tasks,
    )

    response = TestClient(app).post(
        "/api/planning/propose",
        json={"objective": "Inspect local machine health."},
    )

    assert response.status_code == 201, response.text
    proposal = response.json()["items"][0]
    saved = tasks.get_task(proposal["task_id"])
    assert saved is not None
    assert saved["status"] == "proposed"
    assert saved["metadata"]["execution"] == {
        "steps": [
            {"tool_name": "system.read_resources", "arguments": {}},
            {"tool_name": "system.list_processes", "arguments": {"limit": 5}},
        ]
    }
