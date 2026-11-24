"""MIA-COMP-04 slice 2: terminal execute boundary under attack (real app, real shell, temp root).

Side effect probe: ``touch`` creates a marker file, so each refusal is proven by the file's absence.
"""

import shutil

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.computer.permissions import PermissionPolicy
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

pytestmark = pytest.mark.skipif(shutil.which("bash") is None and shutil.which("sh") is None, reason="needs a POSIX shell")
FORGED = "e" * 32


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    policy = PermissionPolicy(tmp_path / "p.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"),
        filesystem=FilesystemAdapter([root], approvals),
        approvals=approvals,
        permission_policy=policy,
    )
    return TestClient(app), root, policy


def body(command="touch marker.txt", **kw):
    return {"command": command, "root_id": "root_1", **kw}


def approved_id(client, **kw):
    pv = client.post("/api/computer/terminal/preview", json=body(**kw))
    assert pv.status_code == 201, pv.text
    aid = pv.json()["approval_id"]
    assert client.post(f"/api/computer/approvals/{aid}/approve").status_code == 200
    return aid


def run(client, aid=None, wait=10, **kw):
    payload = {**body(**kw), "wait_seconds": wait}
    if aid:
        payload["approval_id"] = aid
    return client.post("/api/computer/terminal/execute", json=payload)


def test_no_approval_forged_and_pending_do_nothing(env):
    client, root, _ = env
    assert run(client).status_code == 409
    assert run(client, FORGED).status_code == 409
    pv = client.post("/api/computer/terminal/preview", json=body()).json()["approval_id"]
    assert run(client, pv).status_code == 409
    assert not (root / "marker.txt").exists()


def test_approval_for_one_command_cannot_run_another(env):
    client, root, _ = env
    aid = approved_id(client, command="echo harmless")
    assert run(client, aid, command="touch marker.txt").status_code == 409
    assert not (root / "marker.txt").exists()


def test_approval_for_one_directory_cannot_run_in_another(env):
    client, root, _ = env
    aid = approved_id(client)
    assert run(client, aid, relative_path="sub").status_code == 409
    assert not (root / "marker.txt").exists() and not (root / "sub/marker.txt").exists()


def test_approval_is_single_use(env):
    client, root, _ = env
    aid = approved_id(client)
    first = run(client, aid)
    assert first.status_code == 200 and (root / "marker.txt").exists()
    (root / "marker.txt").unlink()
    assert run(client, aid).status_code == 409
    assert not (root / "marker.txt").exists()


def test_stop_all_blocks_a_valid_approved_command_until_resume(env):
    client, root, _ = env
    aid = approved_id(client)
    assert client.post("/api/computer/stop-all").status_code == 200
    blocked = run(client, aid)
    assert blocked.status_code >= 400 and not (root / "marker.txt").exists()
    assert client.post("/api/computer/resume").status_code == 200
    again = run(client, approved_id(client))
    assert again.status_code == 200 and (root / "marker.txt").exists()


def test_grant_never_covers_high_risk_and_revocation_takes_effect(env):
    client, root, policy = env
    r = client.post(
        "/api/computer/permissions",
        json={"capabilities": ["terminal.run"], "level": "execute", "duration_seconds": 600},
    )
    assert r.status_code == 201, r.text
    grant_id = r.json()["grant_id"]
    assert run(client).status_code == 200  # medium risk is covered by the grant
    (root / "marker.txt").unlink(missing_ok=True)
    high = run(client, command="rm -rf sub")
    assert high.status_code >= 400 and (root / "sub").exists()
    assert client.delete(f"/api/computer/permissions/{grant_id}").status_code == 200
    assert run(client).status_code == 409 and not (root / "marker.txt").exists()
