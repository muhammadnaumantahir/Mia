import pytest

from mia.agent.executor import TaskExecutionError, TaskToolExecutor
from mia.agent.tools import ToolRegistry


def make_registry(mode="read"):
    registry = ToolRegistry()
    registry.register(
        name="demo.inspect",
        description="Return a bounded local test value.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string", "maxLength": 20}},
            "required": ["value"],
            "additionalProperties": False,
        },
        permission="demo.inspect",
        mode=mode,
        handler=lambda args: {"value": args["value"]},
    )
    return registry


@pytest.mark.asyncio
async def test_task_executor_runs_exact_registered_read_tool():
    executor = TaskToolExecutor(make_registry())
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "tool_name": "demo.inspect",
                "arguments": {"value": "safe"},
            }
        },
    }

    result = await executor.execute(task)

    assert result == {
        "tool_name": "demo.inspect",
        "result": {"value": "safe"},
    }


@pytest.mark.asyncio
async def test_task_executor_rejects_preview_tools():
    executor = TaskToolExecutor(make_registry(mode="preview"))
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "tool_name": "demo.inspect",
                "arguments": {"value": "safe"},
            }
        },
    }

    with pytest.raises(TaskExecutionError, match="read-only"):
        await executor.execute(task)


@pytest.mark.asyncio
async def test_task_executor_does_not_treat_task_goal_as_a_command():
    executor = TaskToolExecutor(make_registry())
    task = {
        "status": "approved",
        "goal": "run anything here",
        "metadata": {},
    }

    with pytest.raises(TaskExecutionError, match="metadata.execution"):
        await executor.execute(task)


@pytest.mark.asyncio
async def test_task_executor_rejects_unapproved_task():
    executor = TaskToolExecutor(make_registry())
    task = {
        "status": "proposed",
        "metadata": {
            "execution": {
                "tool_name": "demo.inspect",
                "arguments": {"value": "safe"},
            }
        },
    }

    with pytest.raises(TaskExecutionError, match="approved"):
        await executor.execute(task)
