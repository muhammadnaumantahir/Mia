"""MIA-COMP-09: pending approval cards survive a page reload and are dropped when spent."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.computer import approvals as approvals_module
from mia.computer.approvals import MAX_CARD_BYTES, ApprovalStore
from mia.computer.filesystem import FilesystemAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from tests.test_chat_tools import ToolCallingProvider, build_client


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))



def make(tmp_path, monkeypatch, content="Created only after approval."):
    root = tmp_path / "allowed"
    root.mkdir(exist_ok=True)
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    provider = ToolCallingProvider(
        "filesystem.preview_write",
        {"root_id": "root_1", "relative_path": "notes/new.txt", "content": content},
    )
    client = build_client(tmp_path, monkeypatch, provider, filesystem, approvals)
    pending = client.post("/api/gateway/chat", json={"prompt": "Create the note", "use_tools": True}).json()[
        "pending_actions"][0]
    return client, approvals, filesystem, pending, root


def cards(client):
    return client.get("/api/computer/approvals/cards").json()["items"]


def test_card_from_a_chat_tool_is_restorable_and_executes_after_a_reload(tmp_path, monkeypatch):
    client, approvals, filesystem, pending, root = make(tmp_path, monkeypatch)
    restored = cards(client)
    assert len(restored) == 1
    card = restored[0]
    assert card["approval_id"] == pending["approval_id"] and card["kind"] == "filesystem.write"
    assert card["payload"] == pending["payload"] and card["display"] == pending["display"]
    assert card["restorable"] is True and card["expires_at"] and card["summary"]

    # a "reload": a brand new app over the same approval database still has the card
    fresh = TestClient(create_app(store=ObservatoryStore(tmp_path / "other.sqlite3"),
                                  filesystem=filesystem, approvals=approvals))
    assert [c["approval_id"] for c in cards(fresh)] == [pending["approval_id"]]
    done = fresh.post(f"/api/approvals/{card['approval_id']}/execute",
                      json={"kind": card["kind"], "payload": card["payload"]})
    assert done.status_code == 200
    assert (root / "notes" / "new.txt").read_text(encoding="utf-8") == "Created only after approval."
    assert cards(fresh) == []


def test_rejected_card_disappears_and_its_content_is_not_kept(tmp_path, monkeypatch):
    client, approvals, _fs, pending, _root = make(tmp_path, monkeypatch)
    assert client.post(f"/api/approvals/{pending['approval_id']}/reject").status_code == 200
    with approvals._connection() as db:  # checked before any listing could purge it
        stored = db.execute("SELECT card_json FROM action_approvals WHERE approval_id = ?",
                            (pending["approval_id"],)).fetchone()[0]
    assert stored is None
    assert cards(client) == []


def test_expired_card_is_purged(tmp_path, monkeypatch):
    client, approvals, _fs, pending, _root = make(tmp_path, monkeypatch)
    monkeypatch.setattr(approvals_module, "_now", lambda: datetime.now(UTC) + timedelta(hours=2))
    assert cards(client) == []
    with approvals._connection() as db:
        assert db.execute("SELECT card_json FROM action_approvals WHERE approval_id = ?",
                          (pending["approval_id"],)).fetchone()[0] is None


def test_card_stays_after_a_failed_attempt_so_the_owner_can_retry(tmp_path, monkeypatch):
    client, _approvals, _fs, pending, root = make(tmp_path, monkeypatch)
    bad = client.post(f"/api/approvals/{pending['approval_id']}/execute",
                      json={"kind": "filesystem.write", "payload": {**pending["payload"], "content": "tampered"}})
    assert bad.status_code == 409
    assert [c["approval_id"] for c in cards(client)] == [pending["approval_id"]]
    assert client.post(f"/api/approvals/{pending['approval_id']}/execute",
                       json={"kind": "filesystem.write", "payload": pending["payload"]}).status_code == 200
    assert (root / "notes" / "new.txt").exists()


def test_oversized_payload_is_not_stored_and_is_marked_not_restorable(tmp_path, monkeypatch):
    client, _approvals, _fs, pending, _root = make(tmp_path, monkeypatch, content="x" * (MAX_CARD_BYTES + 10))
    card = cards(client)[0]
    assert card["approval_id"] == pending["approval_id"]
    assert card["restorable"] is False and card["payload"] == {} and card["summary"]


def test_save_card_only_applies_to_pending_approvals(tmp_path):
    store = ApprovalStore(tmp_path / "a.sqlite3")
    first = store.request(action_id="x", payload={"p": 1}, summary="first")["approval_id"]
    second = store.request(action_id="x", payload={"p": 2}, summary="second")["approval_id"]
    assert store.save_card(first, {"kind": "k", "summary": "first", "payload": {"p": 1}, "display": {}})
    assert store.save_card(second, {"kind": "k", "summary": "second", "payload": {"p": 2}, "display": {}})
    store.approve(second)
    assert store.save_card(second, {"kind": "k"}) is False
    assert store.save_card("missing", {"kind": "k"}) is False
    assert [c["approval_id"] for c in store.pending_cards()] == [first]  # approved one is no longer waiting


def test_a_failing_card_store_never_breaks_the_tool_call(tmp_path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")

    def boom(*args, **kwargs):
        raise RuntimeError("database locked")

    monkeypatch.setattr(approvals, "save_card", boom)
    provider = ToolCallingProvider(
        "filesystem.preview_write", {"root_id": "root_1", "relative_path": "a.txt", "content": "hi"})
    client = build_client(tmp_path, monkeypatch, provider, FilesystemAdapter([root], approvals), approvals)
    response = client.post("/api/gateway/chat", json={"prompt": "Create a.txt", "use_tools": True})
    assert response.status_code == 200 and len(response.json()["pending_actions"]) == 1
