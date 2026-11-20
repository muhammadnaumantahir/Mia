from types import SimpleNamespace

from fastapi.testclient import TestClient

from mia.computer.applications import ApplicationDefinition, ApplicationLauncher
from mia.computer.approvals import ApprovalStore
from mia.computer.capabilities import PermissionLevel
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_application_catalog_preview_and_approval_api(tmp_path, monkeypatch):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    launcher = ApplicationLauncher(
        [ApplicationDefinition("editor", "Text Editor", "editor.exe")],
        approvals,
    )
    monkeypatch.setattr(
        "mia.computer.applications.ApplicationLauncher._resolve_executable",
        staticmethod(lambda executable: "C:/Windows/System32/notepad.exe"),
    )
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            approvals=approvals,
            application_launcher=launcher,
        )
    )
    apps = client.get("/api/computer/apps").json()["items"]
    assert apps[0]["app_id"] == "editor"

    preview = client.post(
        "/api/computer/apps/launch/preview",
        json={"app_id": "editor"},
    )
    assert preview.status_code == 201
    approval_id = preview.json()["approval_id"]

    blocked = client.post(
        "/api/computer/apps/launch/execute",
        json={"app_id": "editor", "approval_id": approval_id},
    )
    assert blocked.status_code == 409

    approved = client.post(f"/api/computer/approvals/{approval_id}/approve")
    assert approved.status_code == 200


def test_application_launch_owner_grant_can_skip_second_approval(tmp_path, monkeypatch):
    approvals = ApprovalStore(tmp_path / "grant-approvals.sqlite3")
    launcher = ApplicationLauncher(
        [ApplicationDefinition("editor", "Text Editor", "editor.exe")],
        approvals,
        launch_check_seconds=0,
    )
    monkeypatch.setattr(
        "mia.computer.applications.ApplicationLauncher._resolve_executable",
        staticmethod(lambda executable: "C:/Windows/System32/notepad.exe"),
    )
    monkeypatch.setattr(
        "mia.computer.applications.subprocess.Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=456, poll=lambda: None),
    )
    app = create_app(
        store=ObservatoryStore(tmp_path / "grant-observatory.sqlite3"),
        approvals=approvals,
        application_launcher=launcher,
    )
    client = TestClient(app)
    app.state.permission_policy.grant(
        granted_by="owner",
        capabilities=["apps.launch"],
        level=PermissionLevel.EXECUTE,
        kind="session",
    )

    preview = client.post("/api/computer/apps/launch/preview", json={"app_id": "editor"})
    assert preview.status_code == 201
    assert preview.json()["requires_approval"] is False
    assert preview.json()["status"] == "approved"

    launched = client.post(
        "/api/computer/apps/launch/execute",
        json={"app_id": "editor", "approval_id": preview.json()["approval_id"]},
    )
    assert launched.status_code == 200
    assert launched.json()["process_id"] == 456
    assert approvals.list_pending() == []
