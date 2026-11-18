import pytest

from mia.agent.tools import ToolRegistry


@pytest.mark.asyncio
async def test_tool_registry_rejects_unknown_tools_and_unexpected_arguments():
    registry = ToolRegistry()
    called = []
    registry.register(
        name="filesystem.read",
        description="Read one configured file.",
        parameters={
            "type": "object",
            "properties": {
                "root_id": {"type": "string", "maxLength": 40},
                "relative_path": {"type": "string", "maxLength": 100},
            },
            "required": ["root_id", "relative_path"],
            "additionalProperties": False,
        },
        permission="filesystem.read",
        mode="read",
        handler=lambda args: called.append(args) or {"ok": True},
    )

    unknown = await registry.execute("shell.execute", {"command": "whoami"})
    assert unknown.output["ok"] is False
    assert "not registered" in unknown.output["error"].lower()

    unexpected = await registry.execute(
        "filesystem.read",
        {"root_id": "root_1", "relative_path": "notes.txt", "admin": True},
    )
    assert unexpected.output["ok"] is False
    assert "unexpected" in unexpected.output["error"].lower()
    assert called == []


@pytest.mark.asyncio
async def test_tool_registry_enforces_integer_bounds_and_string_lengths():
    registry = ToolRegistry()
    called = []
    registry.register(
        name="safe.search",
        description="Bounded search.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 20},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        permission="search.read",
        mode="read",
        handler=lambda args: called.append(args) or {"ok": True},
    )

    too_many = await registry.execute("safe.search", {"query": "ok", "limit": 11})
    too_long = await registry.execute(
        "safe.search",
        {"query": "x" * 21, "limit": 10},
    )
    valid = await registry.execute("safe.search", {"query": "Mia", "limit": 5})
    assert too_many.output["ok"] is False
    assert too_long.output["ok"] is False
    assert valid.output["ok"] is True
    assert len(called) == 1
