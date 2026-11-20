from mia.agent.tools import ToolDefinition
from mia.brain.broker import OwnerPermissionEvaluator
from mia.computer.capabilities import default_registry
from mia.computer.permissions import PermissionLevel, PermissionPolicy


def definition(name, permission, mode):
    return ToolDefinition(
        name=name,
        description="test tool",
        parameters={"type": "object", "properties": {}},
        permission=permission,
        mode=mode,
        handler=lambda _args: {"ok": True},
    )


def test_brain_read_tools_are_checked_against_shared_capability_registry(tmp_path):
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    evaluator = OwnerPermissionEvaluator(policy, default_registry())

    allowed = evaluator.evaluate(
        definition("filesystem.read_text", "filesystem.read", "read"),
        {"root_id": "root_1", "relative_path": "notes.txt"},
    )
    assert allowed.allowed is True
    assert allowed.stage == "ok"

    desktop = evaluator.evaluate(
        definition("desktop.list_windows", "desktop.inspect", "read"),
        {},
    )
    assert desktop.allowed is True


def test_brain_preview_tools_prepare_owner_approval_without_executing(tmp_path):
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    evaluator = OwnerPermissionEvaluator(policy, default_registry())

    preview = evaluator.evaluate(
        definition("filesystem.preview_write", "filesystem.write", "preview"),
        {"root_id": "root_1", "relative_path": "notes.txt", "content": "hello"},
    )
    assert preview.allowed is True
    # The preview handler only creates a pending one-time action. The write
    # itself remains behind the separate adapter approval/execute boundary.


def test_brain_rejects_unknown_tool_permission_mapping(tmp_path):
    evaluator = OwnerPermissionEvaluator(
        PermissionPolicy(tmp_path / "permissions.sqlite3"),
        default_registry(),
    )
    refused = evaluator.evaluate(
        definition("unknown.shell", "shell.execute", "read"),
        {},
    )
    assert refused.allowed is False
    assert refused.stage == "permission"
