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


@pytest.mark.asyncio
async def test_task_executor_rejects_invalid_arguments_before_invoking_tool():
    calls = []
    registry = ToolRegistry()
    registry.register(
        name="demo.inspect",
        description="Read one short string.",
        parameters={
            "type": "object",
            "properties": {"value": {"type": "string", "maxLength": 20}},
            "required": ["value"],
            "additionalProperties": False,
        },
        permission="demo.inspect",
        mode="read",
        handler=lambda args: calls.append(args) or {"value": args["value"]},
    )
    executor = TaskToolExecutor(registry)
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "tool_name": "demo.inspect",
                "arguments": {"value": 123},
            }
        },
    }

    with pytest.raises(TaskExecutionError, match="value must be a string"):
        executor.validate(task)
    assert calls == []


@pytest.mark.asyncio
async def test_task_executor_runs_validated_read_only_steps_in_order():
    calls = []
    registry = ToolRegistry()
    registry.register(
        name="demo.first",
        description="Read the first test value.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("first") or {"value": 1},
    )
    registry.register(
        name="demo.second",
        description="Read the second test value.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("second") or {"value": 2},
    )
    executor = TaskToolExecutor(registry)
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "steps": [
                    {"tool_name": "demo.first", "arguments": {}},
                    {"tool_name": "demo.second", "arguments": {}},
                ]
            }
        },
    }

    result = await executor.execute(task)

    assert calls == ["first", "second"]
    assert result == {
        "step_count": 2,
        "tool_names": ["demo.first", "demo.second"],
        "steps": [
            {"tool_name": "demo.first", "result": {"value": 1}},
            {"tool_name": "demo.second", "result": {"value": 2}},
        ],
    }


@pytest.mark.asyncio
async def test_task_executor_rejects_sequence_with_one_unsafe_step_before_invoking_any_tool():
    calls = []
    registry = ToolRegistry()
    registry.register(
        name="demo.read",
        description="Read a harmless value.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("read") or {"ok": True},
    )
    registry.register(
        name="demo.preview",
        description="Prepare a write preview.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.write",
        mode="preview",
        handler=lambda args: calls.append("preview") or {"ok": True},
    )
    executor = TaskToolExecutor(registry)
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "steps": [
                    {"tool_name": "demo.read", "arguments": {}},
                    {"tool_name": "demo.preview", "arguments": {}},
                ]
            }
        },
    }

    with pytest.raises(TaskExecutionError, match="step 2"):
        executor.validate_steps(task)
    assert calls == []


@pytest.mark.asyncio
async def test_task_executor_stops_sequence_on_first_tool_failure():
    calls = []
    registry = ToolRegistry()
    registry.register(
        name="demo.first",
        description="Read the first value.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("first") or {"value": 1},
    )
    registry.register(
        name="demo.fail",
        description="Return an error for the second step.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("fail") or {"ok": False, "error": "expected"},
    )
    registry.register(
        name="demo.never",
        description="Must not run after a failed step.",
        parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        permission="demo.read",
        mode="read",
        handler=lambda args: calls.append("never") or {"value": 3},
    )
    executor = TaskToolExecutor(registry)
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "steps": [
                    {"tool_name": "demo.first", "arguments": {}},
                    {"tool_name": "demo.fail", "arguments": {}},
                    {"tool_name": "demo.never", "arguments": {}},
                ]
            }
        },
    }

    with pytest.raises(TaskExecutionError, match="step 2 failed"):
        await executor.execute(task)
    assert calls == ["first", "fail"]


def test_task_executor_rejects_oversized_sequences(tmp_path):
    executor = TaskToolExecutor(make_registry())
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "steps": [
                    {"tool_name": "demo.inspect", "arguments": {"value": "x"}}
                    for _ in range(6)
                ]
            }
        },
    }
    with pytest.raises(TaskExecutionError, match="between 2 and 5"):
        executor.validate_steps(task)


@pytest.mark.asyncio
async def test_task_executor_checks_cancellation_before_each_step():
    calls = []
    registry = ToolRegistry()
    for name in ("demo.first", "demo.second"):
        registry.register(
            name=name,
            description="Read a harmless value.",
            parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            permission="demo.read",
            mode="read",
            handler=lambda args, tool=name: calls.append(tool) or {"ok": True},
        )
    executor = TaskToolExecutor(registry)
    task = {
        "status": "approved",
        "metadata": {
            "execution": {
                "steps": [
                    {"tool_name": "demo.first", "arguments": {}},
                    {"tool_name": "demo.second", "arguments": {}},
                ]
            }
        },
    }
    checks = iter([True, False])

    with pytest.raises(TaskExecutionError, match="cancelled or paused"):
        await executor.execute(task, should_continue=lambda: next(checks))

    assert calls == ["demo.first"]
