"""MIA-COMP-09: undo of a completed approved file write (real files, no network)."""

import json

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture()
def env(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    root = tmp_path / "allowed"
    fs = FilesystemAdapter([root], approvals)
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    return TestClient(create_app(store=store, filesystem=fs, approvals=approvals)), store, root


def write(client, content, path="notes/a.txt"):
    body = {"root_id": "root_1", "relative_path": path, "content": content}
    aid = client.post("/api/computer/files/write/preview", json=body).json()["approval_id"]
    done = client.post(f"/api/approvals/{aid}/execute", json={"kind": "filesystem.write", "payload": body})
    assert done.status_code == 200, done.text
    return done.json()["result"]


def undo(client, result, path="notes/a.txt", **overrides):
    body = {"root_id": "root_1", "relative_path": path, "expected_sha256": result["sha256"],
            "backup": result["backup"], **overrides}
    return client.post("/api/computer/files/write/undo", json=body)


def events(store, kind):
    return [json.loads(e["payload_json"]) for e in store.list_events() if e["event_type"] == kind]


def test_undoing_an_overwrite_restores_the_earlier_version_and_keeps_what_it_replaced(env):
    client, store, root = env
    write(client, "version one")
    second = write(client, "version two")
    assert second["backup"]
    response = undo(client, second)
    assert response.status_code == 200 and response.json()["result"]["action"] == "restored_previous_version"
    assert (root / "notes" / "a.txt").read_text() == "version one"
    safety = response.json()["result"]["safety_backup"]
    assert (root / safety).read_text() == "version two"  # the undo itself can be recovered
    assert events(store, "write_undone")[0]["action"] == "restored_previous_version"


def test_undoing_a_new_file_removes_it_but_keeps_a_recovery_copy(env):
    client, _store, root = env
    created = write(client, "brand new")
    assert created["backup"] is None
    response = undo(client, created)
    assert response.status_code == 200 and response.json()["result"]["action"] == "removed_created_file"
    assert not (root / "notes" / "a.txt").exists()
    assert (root / response.json()["result"]["safety_backup"]).read_text() == "brand new"


def test_undo_is_refused_if_the_file_changed_since_mia_wrote_it(env):
    client, store, root = env
    write(client, "version one")
    second = write(client, "version two")
    (root / "notes" / "a.txt").write_text("my own later edit")
    response = undo(client, second)
    assert response.status_code == 409 and "changed since" in response.json()["detail"]
    assert (root / "notes" / "a.txt").read_text() == "my own later edit"
    assert events(store, "write_undo_refused") and not events(store, "write_undone")


def test_a_second_undo_of_the_same_write_is_refused(env):
    client, _store, root = env
    write(client, "version one")
    second = write(client, "version two")
    assert undo(client, second).status_code == 200
    again = undo(client, second)
    assert again.status_code == 409
    assert (root / "notes" / "a.txt").read_text() == "version one"


def test_undo_of_a_deleted_file_has_nothing_to_do(env):
    client, _store, root = env
    created = write(client, "x")
    (root / "notes" / "a.txt").unlink()
    assert undo(client, created).status_code == 409


@pytest.mark.parametrize("backup", [
    "../secret.txt", "/etc/passwd", ".mia-recovery/backups/../../outside-a.txt",
    ".mia-recovery/deleted/x-a.txt", ".mia-recovery/backups/sub/x-a.txt",
    ".mia-recovery/backups/20250101T000000000000Z-other.txt", "notes/a.txt",
])
def test_undo_only_accepts_a_recovery_copy_of_the_same_file(env, backup):
    client, _store, root = env
    write(client, "version one")
    second = write(client, "version two")
    (root / "outside-a.txt").write_text("not a backup")
    response = undo(client, second, backup=backup)
    assert response.status_code in {403, 409}
    assert (root / "notes" / "a.txt").read_text() == "version two"


def test_missing_or_pruned_backup_is_reported_without_touching_the_file(env):
    client, _store, root = env
    write(client, "version one")
    second = write(client, "version two")
    (root / second["backup"]).unlink()
    response = undo(client, second)
    assert response.status_code == 409 and "no longer available" in response.json()["detail"]
    assert (root / "notes" / "a.txt").read_text() == "version two"


@pytest.mark.parametrize("path", [".env", "keys/server.pem", ".git/config"])
def test_undo_cannot_be_aimed_at_blocked_files(env, path):
    client, _store, root = env
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret")
    import hashlib
    digest = hashlib.sha256(b"secret").hexdigest()
    response = client.post("/api/computer/files/write/undo", json={
        "root_id": "root_1", "relative_path": path, "expected_sha256": digest, "backup": None})
    assert response.status_code == 403 and target.read_text() == "secret"


def test_undo_input_is_validated(env):
    client, *_ = env
    base = {"root_id": "root_1", "relative_path": "a.txt", "expected_sha256": "0" * 64}
    assert client.post("/api/computer/files/write/undo", json={**base, "expected_sha256": "nothex"}).status_code == 422
    assert client.post("/api/computer/files/write/undo", json={**base, "extra": 1}).status_code == 422
    assert client.post("/api/computer/files/write/undo", json={**base, "root_id": "nope"}).status_code == 403


def test_a_real_recovery_copy_of_a_different_file_cannot_be_restored_over_this_one(env):
    client, _store, root = env
    write(client, "version one")
    second = write(client, "version two")
    other = root / ".mia-recovery" / "backups" / "20250101T000000000000Z-other.txt"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text("OTHER FILE CONTENT")
    response = undo(client, second, backup=str(other.relative_to(root)))
    assert response.status_code == 403 and "not a recovery copy of this file" in response.json()["detail"]
    assert (root / "notes" / "a.txt").read_text() == "version two"
