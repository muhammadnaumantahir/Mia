"""MIA-COMP-04: adversarial checks that approvals and grants are enforced at the execute boundary.

Real FastAPI app, real filesystem adapter and SQLite stores in a temp dir; nothing is mocked except
the clock in the expiry test. Each attack asserts both the HTTP refusal and that the filesystem
did not change, because a refusal that still performed the action would be the real bug.
"""

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

FORGED = "f" * 32


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "allowed"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    fs = FilesystemAdapter([root], approvals)
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), filesystem=fs, approvals=approvals))
    return client, root, approvals


def write_body(path="a/note.txt", content="hello", **extra):
    return {"root_id": "root_1", "relative_path": path, "content": content, **extra}


def preview(client, **kw):
    r = client.post("/api/computer/files/write/preview", json=write_body(**kw))
    assert r.status_code == 201, r.text
    return r.json()["approval_id"]


def test_execute_without_approval_or_grant_is_refused(env):
    client, root, _ = env
    r = client.post("/api/computer/files/write/execute", json=write_body())
    assert r.status_code == 409 and not (root / "a/note.txt").exists()


def test_forged_approval_id_is_refused(env):
    client, root, _ = env
    r = client.post("/api/computer/files/write/execute", json=write_body(approval_id=FORGED))
    assert r.status_code >= 400 and not (root / "a/note.txt").exists()


def test_pending_unapproved_preview_cannot_execute(env):
    client, root, _ = env
    approval = preview(client)
    r = client.post("/api/computer/files/write/execute", json=write_body(approval_id=approval))
    assert r.status_code == 409 and not (root / "a/note.txt").exists()


def test_approval_for_other_content_or_path_is_refused(env):
    client, root, _ = env
    approval = preview(client)
    assert client.post(f"/api/computer/approvals/{approval}/approve").status_code == 200
    for body in (write_body(content="EVIL"), write_body(path="b/note.txt")):
        r = client.post("/api/computer/files/write/execute", json={**body, "approval_id": approval})
        assert r.status_code >= 400, body
    assert not (root / "a/note.txt").exists() and not (root / "b/note.txt").exists()


def test_approval_is_single_use(env):
    client, root, _ = env
    approval = preview(client)
    client.post(f"/api/computer/approvals/{approval}/approve")
    ok = client.post("/api/computer/files/write/execute", json=write_body(approval_id=approval))
    assert ok.status_code == 200
    (root / "a/note.txt").write_text("changed by owner")
    replay = client.post("/api/computer/files/write/execute", json=write_body(approval_id=approval))
    assert replay.status_code >= 400
    assert (root / "a/note.txt").read_text() == "changed by owner"


def test_rejected_approval_cannot_be_approved_or_used(env):
    client, root, _ = env
    approval = preview(client)
    assert client.post(f"/api/approvals/{approval}/reject").status_code in {200, 404, 409}
    client.post(f"/api/computer/approvals/{approval}/approve")
    r = client.post("/api/computer/files/write/execute", json=write_body(approval_id=approval))
    assert r.status_code >= 400 and not (root / "a/note.txt").exists()


def test_expired_approval_is_refused(env, monkeypatch):
    client, root, _ = env
    approval = preview(client)
    client.post(f"/api/computer/approvals/{approval}/approve")
    from datetime import UTC, datetime, timedelta

    import mia.computer.approvals as module

    monkeypatch.setattr(module, "_now", lambda: datetime.now(UTC) + timedelta(hours=2))
    r = client.post("/api/computer/files/write/execute", json=write_body(approval_id=approval))
    assert r.status_code >= 400 and not (root / "a/note.txt").exists()


def grant(client, root, directory):
    r = client.post(
        "/api/computer/permissions",
        json={"capabilities": ["filesystem.write"], "level": "write", "directory": str(directory), "duration_seconds": 600},
    )
    assert r.status_code == 201, r.text
    return r.json()["grant_id"]


def test_grant_is_scoped_to_its_directory(env):
    client, root, _ = env
    grant(client, root, (root / "a").resolve())
    inside = client.post("/api/computer/files/write/execute", json=write_body())
    outside = client.post("/api/computer/files/write/execute", json=write_body(path="b/note.txt"))
    assert inside.status_code == 200 and (root / "a/note.txt").exists()
    assert outside.status_code == 409 and not (root / "b/note.txt").exists()


def test_revoked_grant_stops_unapproved_writes(env):
    client, root, _ = env
    grant_id = grant(client, root, (root / "a").resolve())
    assert client.post("/api/computer/files/write/execute", json=write_body(path="a/one.txt")).status_code == 200
    assert client.delete(f"/api/computer/permissions/{grant_id}").status_code == 200
    r = client.post("/api/computer/files/write/execute", json=write_body(path="a/two.txt"))
    assert r.status_code == 409 and not (root / "a/two.txt").exists()


def test_revoke_all_stops_grant_use_and_grant_cannot_cover_other_capability(env):
    client, root, _ = env
    grant(client, root, (root / "a").resolve())
    # a write grant must not authorise a delete
    (root / "a/keep.txt").write_text("keep")
    r = client.post(
        "/api/computer/files/ops/execute",
        json={"op": "delete", "root_id": "root_1", "relative_path": "a/keep.txt"},
    )
    assert r.status_code >= 400 and (root / "a/keep.txt").exists()
    assert client.post("/api/computer/permissions/revoke-all").status_code == 200
    r = client.post("/api/computer/files/write/execute", json=write_body())
    assert r.status_code == 409 and not (root / "a/note.txt").exists()


def test_file_op_forged_and_unapproved_delete_refused(env):
    client, root, _ = env
    (root / "a/keep.txt").write_text("keep")
    body = {"op": "delete", "root_id": "root_1", "relative_path": "a/keep.txt"}
    assert client.post("/api/computer/files/ops/execute", json=body).status_code >= 400
    forged = client.post("/api/computer/files/ops/execute", json={**body, "approval_id": FORGED})
    assert forged.status_code >= 400
    assert (root / "a/keep.txt").read_text() == "keep"


def test_file_op_approval_for_one_target_cannot_delete_another(env):
    client, root, _ = env
    (root / "a/x.txt").write_text("x")
    (root / "a/y.txt").write_text("y")
    pv = client.post("/api/computer/files/ops/preview", json={"op": "delete", "root_id": "root_1", "relative_path": "a/x.txt"})
    assert pv.status_code == 201, pv.text
    approval = pv.json()["approval_id"]
    client.post(f"/api/computer/approvals/{approval}/approve")
    r = client.post(
        "/api/computer/files/ops/execute",
        json={"op": "delete", "root_id": "root_1", "relative_path": "a/y.txt", "approval_id": approval},
    )
    assert r.status_code >= 400 and (root / "a/y.txt").exists()
