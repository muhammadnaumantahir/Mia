from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_filesystem_api_requires_payload_bound_approval(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([tmp_path / "allowed"], approvals)
    store = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            filesystem=filesystem,
            approvals=approvals,
        )
    )

    roots = client.get("/api/computer/roots").json()["items"]
    assert roots[0]["root_id"] == "root_1"

    preview = client.post(
        "/api/computer/files/write/preview",
        json={
            "root_id": "root_1",
            "relative_path": "docs/hello.txt",
            "content": "Mia can edit this after approval.",
        },
    )
    assert preview.status_code == 201
    approval_id = preview.json()["approval_id"]

    blocked = client.post(
        "/api/computer/files/write/execute",
        json={
            "root_id": "root_1",
            "relative_path": "docs/hello.txt",
            "content": "Mia can edit this after approval.",
            "approval_id": approval_id,
        },
    )
    assert blocked.status_code == 409

    approved = client.post(f"/api/computer/approvals/{approval_id}/approve")
    assert approved.status_code == 200

    executed = client.post(
        "/api/computer/files/write/execute",
        json={
            "root_id": "root_1",
            "relative_path": "docs/hello.txt",
            "content": "Mia can edit this after approval.",
            "approval_id": approval_id,
        },
    )
    assert executed.status_code == 200

    read = client.get(
        "/api/computer/file",
        params={"root_id": "root_1", "relative_path": "docs/hello.txt"},
    )
    assert read.json()["content"] == "Mia can edit this after approval."
    assert store.list_events()[0]["category"] == "computer"


def test_computer_api_blocks_path_traversal(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([tmp_path / "allowed"], approvals)
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "obs.sqlite3"),
            filesystem=filesystem,
            approvals=approvals,
        )
    )
    response = client.get(
        "/api/computer/files",
        params={"root_id": "root_1", "relative_path": "../"},
    )
    assert response.status_code == 403


def test_direct_filesystem_write_can_use_a_scoped_owner_grant(tmp_path):
    from mia.computer.capabilities import PermissionLevel

    approvals = ApprovalStore(tmp_path / "grant-approvals.sqlite3")
    root = tmp_path / "grant-allowed"
    filesystem = FilesystemAdapter([root], approvals)
    app = create_app(
        store=ObservatoryStore(tmp_path / "grant-observatory.sqlite3"),
        filesystem=filesystem,
        approvals=approvals,
    )
    client = TestClient(app)
    grant = app.state.permission_policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(root),
        kind="session",
    )
    preview = client.post(
        "/api/computer/files/write/preview",
        json={"root_id": "root_1", "relative_path": "notes.txt", "content": "Granted write"},
    )
    assert preview.status_code == 201
    assert preview.json()["requires_approval"] is False
    assert preview.json()["status"] == "approved"

    executed = client.post(
        "/api/computer/files/write/execute",
        json={
            "root_id": "root_1",
            "relative_path": "notes.txt",
            "content": "Granted write",
            "approval_id": preview.json()["approval_id"],
        },
    )
    assert executed.status_code == 200
    assert (root / "notes.txt").read_text(encoding="utf-8") == "Granted write"
    assert app.state.permission_policy.revoke(grant["grant_id"])


def test_direct_filesystem_write_without_grant_still_requires_approval(tmp_path):
    approvals = ApprovalStore(tmp_path / "no-grant-approvals.sqlite3")
    filesystem = FilesystemAdapter([tmp_path / "no-grant-root"], approvals)
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "no-grant-observatory.sqlite3"),
            filesystem=filesystem,
            approvals=approvals,
        )
    )
    preview = client.post(
        "/api/computer/files/write/preview",
        json={"root_id": "root_1", "relative_path": "notes.txt", "content": "Must approve"},
    )
    assert preview.status_code == 201
    assert preview.json()["requires_approval"] is True
    blocked = client.post(
        "/api/computer/files/write/execute",
        json={"root_id": "root_1", "relative_path": "notes.txt", "content": "Must approve"},
    )
    assert blocked.status_code == 409
