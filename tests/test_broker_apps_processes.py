"""MIA-COMP-04 slice 3: application launch and process termination under attack (HTTP level).

App launch uses a recording stand-in for ``subprocess`` so a refusal is proven by *no spawn call*;
process termination uses a real throwaway child process so a refusal is proven by it still running.
"""

import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mia.computer.applications import ApplicationDefinition, ApplicationLauncher
from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.computer.processes import ProcessManager
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

FORGED = "d" * 32


@pytest.fixture
def env(tmp_path, monkeypatch):
    spawned: list[list[str]] = []

    class Recorder:
        def __init__(self, argv, **kwargs):
            spawned.append(argv)
            self.pid = 4242

        def poll(self):
            return None

    monkeypatch.setattr("mia.computer.applications.subprocess", SimpleNamespace(Popen=Recorder))
    root = tmp_path / "root"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    launcher = ApplicationLauncher(
        [
            ApplicationDefinition("editor", "Editor", sys.executable),
            ApplicationDefinition("viewer", "Viewer", sys.executable, arguments=("-V",)),
        ],
        approvals,
        launch_check_seconds=0,
    )
    manager = ProcessManager(approvals, sample_interval=0.05)
    app = create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"),
        approvals=approvals,
        application_launcher=launcher,
        process_manager=manager,
        filesystem=FilesystemAdapter([root], approvals),
    )
    return TestClient(app), spawned, root


@pytest.fixture
def child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def launch_preview(client, app_id="editor"):
    r = client.post("/api/computer/apps/launch/preview", json={"app_id": app_id})
    assert r.status_code == 201, r.text
    return r.json()["approval_id"]


def launch(client, app_id="editor", approval_id=None):
    body = {"app_id": app_id}
    if approval_id:
        body["approval_id"] = approval_id
    return client.post("/api/computer/apps/launch/execute", json=body)


# ---- application launch ------------------------------------------------------------------
def test_launch_without_approval_forged_or_pending_never_spawns(env):
    client, spawned, _ = env
    assert launch(client).status_code == 409
    assert launch(client, approval_id=FORGED).status_code == 409
    assert launch(client, approval_id=launch_preview(client)).status_code == 409
    assert spawned == []


def test_launch_approval_is_bound_to_the_app_and_single_use(env):
    client, spawned, _ = env
    aid = launch_preview(client, "editor")
    client.post(f"/api/computer/approvals/{aid}/approve")
    assert launch(client, "viewer", aid).status_code == 409 and spawned == []
    assert launch(client, "editor", aid).status_code == 200 and len(spawned) == 1
    assert launch(client, "editor", aid).status_code == 409 and len(spawned) == 1


def test_an_approval_for_a_different_action_cannot_launch_an_app(env):
    client, spawned, root = env
    pv = client.post("/api/computer/files/write/preview",
                     json={"root_id": "root_1", "relative_path": "a.txt", "content": "x"})
    assert pv.status_code == 201, pv.text
    aid = pv.json()["approval_id"]
    client.post(f"/api/computer/approvals/{aid}/approve")
    assert launch(client, "editor", aid).status_code >= 400 and spawned == []
    assert not (root / "a.txt").exists()


def test_launch_not_in_allowlist_is_refused_even_with_a_grant(env):
    client, spawned, _ = env
    client.post("/api/computer/permissions", json={"capabilities": ["apps.launch"], "level": "execute", "duration_seconds": 600})
    assert launch(client, "calculator").status_code >= 400 and spawned == []


def test_launch_grant_allows_then_revocation_and_stop_all_block(env):
    client, spawned, _ = env
    r = client.post("/api/computer/permissions", json={"capabilities": ["apps.launch"], "level": "execute", "duration_seconds": 600})
    assert r.status_code == 201, r.text
    gid = r.json()["grant_id"]
    assert launch(client).status_code == 200 and len(spawned) == 1
    client.post("/api/computer/stop-all")
    assert launch(client).status_code >= 400 and len(spawned) == 1
    client.post("/api/computer/resume")
    assert client.delete(f"/api/computer/permissions/{gid}").status_code == 200
    assert launch(client).status_code == 409 and len(spawned) == 1


# ---- process termination -----------------------------------------------------------------
def term_body(pid, approval_id=None, force=False):
    body = {"pid": pid, "force": force}
    if approval_id:
        body["approval_id"] = approval_id
    return body


def test_terminate_without_forged_or_pending_approval_leaves_process_running(env, child):
    client, *_ = env
    assert client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid, FORGED)).status_code == 409
    pv = client.post("/api/computer/processes/terminate/preview", json=term_body(child.pid))
    assert pv.status_code == 201, pv.text
    pending = client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid, pv.json()["approval_id"]))
    assert pending.status_code == 409 and child.poll() is None


def test_terminate_approval_is_bound_to_pid_and_force_flag(env, child):
    client, *_ = env
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        pv = client.post("/api/computer/processes/terminate/preview", json=term_body(child.pid)).json()
        client.post(f"/api/computer/approvals/{pv['approval_id']}/approve")
        wrong_pid = client.post("/api/computer/processes/terminate/execute", json=term_body(other.pid, pv["approval_id"]))
        forced = client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid, pv["approval_id"], force=True))
        assert wrong_pid.status_code >= 400 and forced.status_code >= 400
        assert child.poll() is None and other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_terminate_grant_never_waives_per_action_approval(env, child):
    client, *_ = env
    client.post("/api/computer/permissions", json={"capabilities": ["process.terminate"], "level": "execute", "duration_seconds": 600})
    r = client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid))
    assert r.status_code >= 400 and child.poll() is None


def test_terminate_refuses_mia_itself_even_when_approved(env):
    import os

    client, *_ = env
    assert client.post("/api/computer/processes/terminate/preview", json=term_body(os.getpid())).status_code == 403
    assert client.post("/api/computer/processes/terminate/execute", json=term_body(os.getpid(), FORGED)).status_code >= 400


def test_terminate_with_exact_approval_works_once(env, child):
    client, *_ = env
    pv = client.post("/api/computer/processes/terminate/preview", json=term_body(child.pid)).json()
    client.post(f"/api/computer/approvals/{pv['approval_id']}/approve")
    ok = client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid, pv["approval_id"]))
    assert ok.status_code == 200, ok.text
    child.wait(timeout=10)
    again = client.post("/api/computer/processes/terminate/execute", json=term_body(child.pid, pv["approval_id"]))
    assert again.status_code >= 400
