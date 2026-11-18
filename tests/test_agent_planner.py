import json
import pytest
from mia.agent.discovery import WorkloadDiscovery
from mia.agent.planner import PlanningError, TaskPlanner
from mia.agent.tasks import TaskStore
from mia.gateway.service import CompletionResult

class FakeGateway:
    def __init__(self, content, tool_calls=()):
        self.content = content
        self.tool_calls = tool_calls
        self.last_policy = None
    async def discover_models(self):
        return [object()]
    async def complete(self, request, candidates, policy):
        self.last_policy = policy
        return CompletionResult(provider_id="local", model_id="test", content=self.content,
            attempted_candidates=("local/test",), tool_calls=self.tool_calls, data_local=True)

@pytest.mark.asyncio
async def test_planner_creates_proposals_without_approving_them(tmp_path):
    gateway = FakeGateway(json.dumps({"tasks": [{
        "title": "Review stale dependencies",
        "goal": "Inspect dependency metadata and report outdated packages.",
        "risk_level": "low", "priority": 60,
        "evidence_summary": "Objective requests a dependency review.",
    }]}))
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    result = await TaskPlanner(gateway, WorkloadDiscovery(tasks)).propose(
        objective="Improve dependency health.")
    assert len(result) == 1
    assert result[0]["status"] == "proposed"
    assert result[0]["requires_governance_review"] is True
    assert tasks.get_task(result[0]["task_id"])["status"] == "proposed"
    assert gateway.last_policy.allow_paid is False

@pytest.mark.asyncio
async def test_planner_rejects_tool_calls_and_invalid_json(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    with pytest.raises(PlanningError, match="tool calls"):
        await TaskPlanner(FakeGateway("{}", ({"name": "desktop.type_text"},)),
            WorkloadDiscovery(tasks)).propose(objective="Check my notes")
    with pytest.raises(PlanningError, match="valid JSON"):
        await TaskPlanner(FakeGateway("not json"),
            WorkloadDiscovery(tasks)).propose(objective="Check my notes")

@pytest.mark.asyncio
async def test_planner_rejects_invalid_task_schema_and_bounds(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    invalid = {"tasks": [{"title":"Do something","goal":"Do something",
        "risk_level":"low","priority":500,"evidence_summary":"No evidence"}]}
    planner = TaskPlanner(FakeGateway(json.dumps(invalid)), WorkloadDiscovery(tasks))
    with pytest.raises(PlanningError, match="priority"):
        await planner.propose(objective="Create plan")
    with pytest.raises(ValueError, match="max_tasks"):
        await planner.propose(objective="Create plan", max_tasks=100)
