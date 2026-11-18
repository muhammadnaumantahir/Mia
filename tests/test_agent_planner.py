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
        return CompletionResult(
            provider_id="local",
            model_id="test",
            content=self.content,
            attempted_candidates=("local/test",),
            tool_calls=self.tool_calls,
            data_local=True,
        )


@pytest.mark.asyncio
async def test_planner_creates_proposals_without_approving_them(tmp_path):
    gateway = FakeGateway(
        json.dumps(
            {
                "tasks": [
                    {
                        "title": "Review stale dependencies",
                        "goal": "Inspect dependency metadata and report outdated packages.",
                        "risk_level": "low",
                        "priority": 60,
                        "evidence_summary": "Objective requests a dependency review.",
                        "execution": None,
                    }
                ]
            }
        )
    )
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    result = await TaskPlanner(gateway, WorkloadDiscovery(tasks)).propose(
        objective="Improve dependency health."
    )

    assert len(result) == 1
    assert result[0]["status"] == "proposed"
    assert result[0]["requires_governance_review"] is True
    assert tasks.get_task(result[0]["task_id"])["status"] == "proposed"
    assert gateway.last_policy.allow_paid is False


@pytest.mark.asyncio
async def test_planner_rejects_tool_calls_and_invalid_json(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    planner = TaskPlanner(
        FakeGateway("{}", ({"name": "desktop.type_text"},)),
        WorkloadDiscovery(tasks),
    )
    with pytest.raises(PlanningError, match="tool calls"):
        await planner.propose(objective="Check my notes")

    planner = TaskPlanner(FakeGateway("not json"), WorkloadDiscovery(tasks))
    with pytest.raises(PlanningError, match="valid JSON"):
        await planner.propose(objective="Check my notes")


@pytest.mark.asyncio
async def test_planner_rejects_invalid_task_schema_and_bounds(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    invalid = {
        "tasks": [
            {
                "title": "Do something",
                "goal": "Do something",
                "risk_level": "low",
                "priority": 500,
                "evidence_summary": "No evidence",
                "execution": None,
            }
        ]
    }
    planner = TaskPlanner(FakeGateway(json.dumps(invalid)), WorkloadDiscovery(tasks))

    with pytest.raises(PlanningError, match="priority"):
        await planner.propose(objective="Create plan")
    with pytest.raises(ValueError, match="max_tasks"):
        await planner.propose(objective="Create plan", max_tasks=100)


def make_read_registry(mode="read"):
    from mia.agent.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register(
        name="system.read_resources",
        description="Read CPU and memory usage.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        permission="system.inspect",
        mode=mode,
        handler=lambda args: {"cpu_percent": 10, "memory_percent": 20},
    )
    return registry


@pytest.mark.asyncio
async def test_planner_attaches_only_validated_registered_read_tool(tmp_path):
    content = {
        "tasks": [
            {
                "title": "Check local resource pressure",
                "goal": "Read current CPU and memory usage.",
                "risk_level": "low",
                "priority": 60,
                "evidence_summary": "The user asked for a local health check.",
                "execution": {
                    "tool_name": "system.read_resources",
                    "arguments": {},
                },
            }
        ]
    }
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    planner = TaskPlanner(
        FakeGateway(json.dumps(content)),
        WorkloadDiscovery(tasks),
        tools=make_read_registry(),
    )

    proposals = await planner.propose(objective="Check resource pressure")

    assert proposals[0]["status"] == "proposed"
    saved = tasks.get_task(proposals[0]["task_id"])
    assert saved["status"] == "proposed"
    assert saved["metadata"]["execution"] == {
        "tool_name": "system.read_resources",
        "arguments": {},
    }


@pytest.mark.asyncio
async def test_planner_rejects_unregistered_or_preview_execution_tools(tmp_path):
    for mode in ("missing", "preview"):
        content = {
            "tasks": [
                {
                    "title": "Attempt unsafe tool selection",
                    "goal": "Attempt to use a tool not permitted for task execution.",
                    "risk_level": "low",
                    "priority": 50,
                    "evidence_summary": "Validation must reject this proposal.",
                    "execution": {
                        "tool_name": "system.read_resources",
                        "arguments": {},
                    },
                }
            ]
        }
        registry = None if mode == "missing" else make_read_registry(mode="preview")
        planner = TaskPlanner(
            FakeGateway(json.dumps(content)),
            WorkloadDiscovery(TaskStore(tmp_path / f"{mode}.sqlite3")),
            tools=registry,
        )
        with pytest.raises(PlanningError, match="tool|registry"):
            await planner.propose(objective="Try tool selection")


@pytest.mark.asyncio
async def test_planner_accepts_short_sequence_of_registered_read_tools(tmp_path):
    registry = make_read_registry()
    from mia.agent.tools import ToolRegistry

    registry.register(
        name="system.list_processes",
        description="List bounded process metadata.",
        parameters={
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            "required": [],
            "additionalProperties": False,
        },
        permission="process.inspect",
        mode="read",
        handler=lambda args: {"items": []},
    )
    planned_steps = [
        {"tool_name": "system.read_resources", "arguments": {}},
        {"tool_name": "system.list_processes", "arguments": {"limit": 5}},
    ]
    gateway = FakeGateway(json.dumps({
        "tasks": [{
            "title": "Inspect local health",
            "goal": "Read resources, then inspect a bounded process list.",
            "risk_level": "low",
            "priority": 60,
            "evidence_summary": "The owner asked for a local health check.",
            "execution": {"steps": planned_steps},
        }]
    }))
    tasks = TaskStore(tmp_path / "multi-plan.sqlite3")
    planner = TaskPlanner(gateway, WorkloadDiscovery(tasks), tools=registry)

    proposals = await planner.propose(objective="Check my local health")

    saved = tasks.get_task(proposals[0]["task_id"])
    assert saved["status"] == "proposed"
    assert saved["metadata"]["execution"] == {"steps": planned_steps}


@pytest.mark.asyncio
async def test_planner_rejects_multistep_sequence_with_unsafe_tool(tmp_path):
    registry = make_read_registry()
    content = {
        "tasks": [{
            "title": "Unsafe sequence",
            "goal": "Mix a safe read and a preview action.",
            "risk_level": "low",
            "priority": 50,
            "evidence_summary": "The sequence must be rejected as a whole.",
            "execution": {
                "steps": [
                    {"tool_name": "system.read_resources", "arguments": {}},
                    {"tool_name": "filesystem.preview_write", "arguments": {
                        "root_id": "workspace",
                        "relative_path": "unsafe.txt",
                        "content": "do not write",
                    }},
                ]
            },
        }]
    }
    planner = TaskPlanner(
        FakeGateway(json.dumps(content)),
        WorkloadDiscovery(TaskStore(tmp_path / "unsafe-multistep.sqlite3")),
        tools=registry,
    )

    with pytest.raises(PlanningError, match="step 2"):
        await planner.propose(objective="Try to mix a read with an unsafe action")
