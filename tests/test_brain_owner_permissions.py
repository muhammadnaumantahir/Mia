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


def test_brain_indexed_file_search_uses_shared_filesystem_search_capability(tmp_path):
    evaluator = OwnerPermissionEvaluator(
        PermissionPolicy(tmp_path / "permissions.sqlite3"),
        default_registry(),
    )
    allowed = evaluator.evaluate(
        definition("knowledge.search_files", "filesystem.search", "read"),
        {"query": "garden"},
    )
    assert allowed.allowed is True
    assert allowed.stage == "ok"


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


def test_brain_app_list_uses_the_declared_discovery_capability(tmp_path):
    evaluator = OwnerPermissionEvaluator(
        PermissionPolicy(tmp_path / "permissions.sqlite3"),
        default_registry(),
    )
    result = evaluator.evaluate(
        definition("applications.list", "process.inspect", "read"),
        {},
    )
    assert result.allowed is True


def test_application_inventory_tool_has_a_registered_owner_capability():
    from mia.computer.capabilities import default_registry

    spec = default_registry().get("apps.discover")
    assert spec.permission.name.lower() == "observe"
    assert spec.risk.name.lower() == "low"
    assert spec.requires_approval_by_default is False



def test_brain_workflow_preview_is_a_registered_read_capability(tmp_path):
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    evaluator = OwnerPermissionEvaluator(policy, default_registry())
    result = evaluator.evaluate(
        definition("workflow.preview_internal", "workflow.preview", "read"),
        {"title": "Plan", "steps": []},
    )
    assert result.allowed is True
    spec = default_registry().get("workflow.preview")
    assert spec.permission == PermissionLevel.READ
    assert spec.risk.name.lower() == "low"


def test_brain_workflow_execution_requires_an_explicit_owner_capability_grant(tmp_path):
    policy = PermissionPolicy(tmp_path / "permissions.sqlite3")
    evaluator = OwnerPermissionEvaluator(policy, default_registry())
    tool = definition("workflow.run_internal", "workflow.run_internal", "write")
    refused = evaluator.evaluate(tool, {"workflow_id": "workflow-1"})
    assert refused.allowed is False
    assert refused.stage == "permission"

    policy.grant(
        granted_by="owner",
        capabilities=["workflow.run_internal"],
        level=PermissionLevel.EXECUTE,
        kind="session",
        note="Allow explicitly requested internal Brain workflows",
    )
    allowed = evaluator.evaluate(tool, {"workflow_id": "workflow-1"})
    assert allowed.allowed is True
    assert default_registry().get("workflow.run_internal").requires_approval_by_default is True
