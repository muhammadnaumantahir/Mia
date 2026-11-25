"""MIA-COMP-04 slice 4: the model-callable tool surface cannot perform side effects.

Runs against the tools the real app registers. The model (or voice) can only reach read tools and
preview tools; a preview must leave the world untouched and yield only a pending approval.
"""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from mia.computer.applications import ApplicationDefinition, ApplicationLauncher
from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

SIDE_EFFECT_WORDS = ("write", "launch", "click", "type", "terminate", "delete", "move", "rename", "execute", "run", "kill", "send")


@pytest.fixture
def env(tmp_path, monkeypatch):
    spawned: list = []
    monkeypatch.setattr("mia.computer.applications.subprocess",
                        SimpleNamespace(Popen=lambda *a, **k: spawned.append(a) or SimpleNamespace(pid=1, poll=lambda: None)))
    root = tmp_path / "root"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    launcher = ApplicationLauncher([ApplicationDefinition("editor", "Editor", sys.executable)], approvals, launch_check_seconds=0)
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), approvals=approvals,
                     application_launcher=launcher, filesystem=FilesystemAdapter([root], approvals))
    return app.state.tool_registry, approvals, root, spawned


def call(registry, name, args):
    return asyncio.run(registry.execute(name, args))


def test_every_registered_tool_is_read_or_preview_and_names_match_the_mode(env):
    registry, *_ = env
    tools = registry._tools
    assert tools, "the real app registers model-callable tools"
    for name, tool in tools.items():
        assert tool.mode in {"read", "preview"}, name
        if tool.mode == "preview":
            assert "preview" in name, f"{name}: preview tools must say so"
        else:
            assert not any(word in name.split(".")[-1].split("_") for word in SIDE_EFFECT_WORDS), (
                f"{name} is registered as read but its name suggests a side effect")


def test_file_write_preview_creates_a_pending_approval_and_no_file(env):
    registry, approvals, root, _ = env
    result = call(registry, "filesystem.preview_write", {"root_id": "root_1", "relative_path": "note.txt", "content": "hi"})
    assert result.pending_action, result
    assert not (root / "note.txt").exists()
    approval_id = result.pending_action.get("approval_id") or result.output.get("approval_id")
    assert approval_id and approvals.status(approval_id) == "pending"


def test_launch_preview_never_spawns_a_process(env):
    registry, approvals, _, spawned = env
    result = call(registry, "applications.preview_launch", {"app_id": "editor"})
    assert result.pending_action and spawned == []


def test_model_cannot_use_a_preview_to_self_approve(env):
    registry, approvals, root, spawned = env
    result = call(registry, "filesystem.preview_write", {"root_id": "root_1", "relative_path": "n.txt", "content": "x"})
    approval_id = result.pending_action.get("approval_id") or result.output.get("approval_id")
    assert approvals.status(approval_id) == "pending"
    assert not hasattr(registry._tools["filesystem.preview_write"], "approve")
    assert not (root / "n.txt").exists() and spawned == []


def test_read_tools_refuse_secrets_and_traversal(env):
    registry, _, root, _ = env
    (root / ".env").write_text("KEY=secret")
    (root.parent / "outside.txt").write_text("outside")
    for rel in (".env", "../outside.txt"):
        result = call(registry, "filesystem.read_text", {"root_id": "root_1", "relative_path": rel})
        assert result.output["ok"] is False and result.output["error_type"] == "FilesystemAccessError", rel
        assert "secret" not in str(result.output) and "outside" not in str(result.output).replace("outside.txt", "")
