"""MIA-COMP-09: approval recovery, duplicate submit, expiry and failure audit (real files, no network)."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.computer import approvals as approvals_module
from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

PAYLOAD = {"root_id": "root_1", "relative_path": "a.txt", "content": "hello"}


@pytest.fixture()
def env(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "allowed"], approvals)
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    client = TestClient(create_app(store=store, filesystem=fs, approvals=approvals))
    return client, approvals, fs, store, tmp_path / "allowed"


def preview(client, **overrides):
    body = {**PAYLOAD, **overrides}
    return client.post("/api/computer/files/write/preview", json=body).json()["approval_id"]


def execute(client, approval_id, **overrides):
    payload = {**PAYLOAD, **overrides}
    return client.post(f"/api/approvals/{approval_id}/execute", json={"kind": "filesystem.write", "payload": payload})


def events(store, event_type):
    return [json.loads(e["payload_json"]) for e in store.list_events() if e["event_type"] == event_type]


def test_failed_attempt_leaves_the_approval_pending_so_the_owner_can_retry(env):
    client, approvals, _fs, store, root = env
    aid = preview(client)
    bad = execute(client, aid, content="HELLO")
    assert bad.status_code == 409 and "changed" in bad.json()["detail"]
    assert approvals.status(aid) == "pending"
    failed = events(store, "approved_action_failed")
    assert failed and failed[0]["approval_released"] is True and failed[0]["approval_state"] == "pending"
    assert not (root / "a.txt").exists()
    assert execute(client, aid).status_code == 200
    assert (root / "a.txt").read_text() == "hello"


def test_failed_attempt_can_be_rejected_instead(env):
    client, approvals, _fs, _store, root = env
    aid = preview(client)
    execute(client, aid, content="HELLO")
    assert client.post(f"/api/approvals/{aid}/reject").status_code == 200
    assert approvals.status(aid) == "rejected"
    assert execute(client, aid).status_code == 409 and not (root / "a.txt").exists()


def test_unknown_action_kind_is_rejected_before_it_can_touch_the_approval(env):
    client, approvals, *_ = env
    aid = preview(client)
    response = client.post(f"/api/approvals/{aid}/execute", json={"kind": "shell.run", "payload": {}})
    assert response.status_code == 422  # request validation stops it first
    assert approvals.status(aid) == "pending"


def test_duplicate_submit_runs_the_action_once(env):
    client, approvals, _fs, store, root = env
    aid = preview(client)
    assert execute(client, aid).status_code == 200
    second = execute(client, aid)
    assert second.status_code == 409 and "already used" in second.json()["detail"]
    assert len(events(store, "approved_tool_action")) == 1
    refused = events(store, "approved_action_refused")
    assert refused and refused[0]["approval_state"] == "consumed"
    assert (root / "a.txt").read_text() == "hello" and approvals.status(aid) == "consumed"


def test_simultaneous_double_click_executes_exactly_once(env):
    client, _approvals, _fs, store, _root = env
    aid = preview(client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = sorted(r.status_code for r in pool.map(lambda _: execute(client, aid), range(4)))
    assert codes.count(200) == 1 and set(codes) <= {200, 409}
    assert len(events(store, "approved_tool_action")) == 1


def test_expired_approval_is_refused_audited_and_writes_nothing(env, monkeypatch):
    client, approvals, _fs, store, root = env
    aid = preview(client)
    later = datetime.now(UTC) + timedelta(hours=2)
    monkeypatch.setattr(approvals_module, "_now", lambda: later)
    response = execute(client, aid)
    assert response.status_code == 409 and "expired" in response.json()["detail"]
    assert approvals.status(aid) == "expired" and not (root / "a.txt").exists()
    assert events(store, "approved_action_refused")[0]["approval_state"] == "expired"


def test_failure_after_the_approval_was_spent_says_so_and_cannot_be_revived(env, monkeypatch):
    client, approvals, fs, store, root = env
    aid = preview(client)

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(fs, "backup_existing", disk_full)
    response = execute(client, aid)
    assert response.status_code == 422 and "approval was used" in response.json()["detail"]
    assert approvals.status(aid) == "consumed" and not (root / "a.txt").exists()
    failed = events(store, "approved_action_failed")[0]
    assert failed["approval_released"] is False and failed["approval_state"] == "consumed"
    assert approvals.release(aid) is False
    assert execute(client, aid).status_code == 409


def test_release_only_applies_to_an_approved_unspent_unexpired_approval(tmp_path, monkeypatch):
    store = ApprovalStore(tmp_path / "a.sqlite3")
    made = store.request(action_id="x", payload={"p": 1}, summary="s")["approval_id"]
    assert store.release(made) is False  # still pending
    store.approve(made)
    assert store.release(made) is True and store.status(made) == "pending"
    store.approve(made)
    store.consume(approval_id=made, action_id="x", payload={"p": 1})
    assert store.release(made) is False  # spent
    other = store.request(action_id="x", payload={"p": 2}, summary="s")["approval_id"]
    store.approve(other)
    monkeypatch.setattr(approvals_module, "_now", lambda: datetime.now(UTC) + timedelta(hours=2))
    assert store.release(other) is False  # expired
    assert store.release("missing") is False


@pytest.mark.parametrize(
    ("setup", "expected"),
    [("approve", "already submitted"), ("reject", "rejected"), ("consume", "already used")],
)
def test_state_messages_are_clear(tmp_path, setup, expected):
    store = ApprovalStore(tmp_path / "a.sqlite3")
    aid = store.request(action_id="x", payload={}, summary="s")["approval_id"]
    if setup == "approve":
        store.approve(aid)
    elif setup == "reject":
        store.reject(aid)
    else:
        store.approve(aid)
        store.consume(approval_id=aid, action_id="x", payload={})
    with pytest.raises(ApprovalError, match=expected):
        store.approve(aid)
