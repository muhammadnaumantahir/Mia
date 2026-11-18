from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.applications import ApplicationDefinition, ApplicationLauncher
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
