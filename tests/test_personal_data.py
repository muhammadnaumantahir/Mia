"""Owner-scoped personal data: tasks, reminders, notes (MIA-LIFE-01)."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mia.memory.context import assemble_memory_context
from mia.memory.personal import PersonalItemNotFoundError
from mia.memory.schema import LATEST_VERSION
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def backdate(store, item_id, days):
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with store._connection() as db:
        db.execute("UPDATE personal_items SET updated_at = ? WHERE item_id = ?", (when, item_id))


def task(store, title="Write the report", **kw):
    return store.personal.add(kind="task", title=title, **kw)


# ------------------------------------------------------------------ model


def test_migration_creates_table_and_defaults(store):
    assert LATEST_VERSION >= 6
    item = store.personal.get(task(store))
    assert item["kind"] == "task" and item["status"] == "open"
    assert item["provenance"] == "owner_provided" and item["private"] is False
    assert item["project"] is None and item["due_at"] is None and item["completed_at"] is None


def test_validation(store):
    with pytest.raises(ValueError):
        store.personal.add(kind="event", title="x")
    with pytest.raises(ValueError):
        store.personal.add(kind="task", title="   ")
    with pytest.raises(ValueError):
        store.personal.add(kind="task", title="x" * 201)
    with pytest.raises(ValueError):
        store.personal.add(kind="task", title="ok", provenance="guess")
    with pytest.raises(ValueError):
        store.personal.add(kind="task", title="ok", project="x" * 65)


def test_reminder_needs_a_timezone_aware_due_time(store):
    with pytest.raises(ValueError):
        store.personal.add(kind="reminder", title="Call Sam")
    with pytest.raises(ValueError):
        store.personal.add(kind="reminder", title="Call Sam", due_at="2026-12-01T09:00:00")
    with pytest.raises(ValueError):
        store.personal.add(kind="task", title="x", due_at="tomorrow")
    rid = store.personal.add(kind="reminder", title="Call Sam", due_at="2026-12-01T09:00:00+05:00")
    assert store.personal.get(rid)["due_at"] == "2026-12-01T04:00:00+00:00"
    with pytest.raises(ValueError):  # a reminder cannot lose its time
        store.personal.update(rid, clear_due=True)


def test_conversation_captured_items_must_cite_their_source(store):
    with pytest.raises(ValueError):
        task(store, provenance="conversation_captured")
    iid = task(store, provenance="conversation_captured",
               source_conversation_id="conv-1", source_message_id="msg-1")
    assert store.personal.get(iid)["source_message_id"] == "msg-1"


def test_credential_like_text_is_refused(store):
    with pytest.raises(ValueError):
        task(store, title="password is hunter2 and key sk-abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(ValueError):
        task(store, details="my key: sk-abcdefghijklmnopqrstuvwxyz123456")
    iid = task(store)
    with pytest.raises(ValueError):
        store.personal.update(iid, details="sk-abcdefghijklmnopqrstuvwxyz123456")


def test_update_tracks_completion_and_reports_changed_fields(store):
    iid = task(store)
    assert store.personal.update(iid, status="done") == ["status"]
    assert store.personal.get(iid)["completed_at"] is not None
    assert store.personal.update(iid, status="done") == []  # no change
    assert store.personal.update(iid, status="open") == ["status"]
    assert store.personal.get(iid)["completed_at"] is None
    with pytest.raises(ValueError):
        store.personal.update(iid, status="archived")
    with pytest.raises(ValueError):
        store.personal.update(iid)
    with pytest.raises(PersonalItemNotFoundError):
        store.personal.update("missing", title="x")


# ------------------------------------------------------------------ projects


def test_project_association_filters_and_isolation(store):
    a = task(store, "Atlas task", project="Atlas")
    b = task(store, "Borealis task", project="Borealis")
    free = task(store, "Loose task")
    assert {i["item_id"] for i in store.personal.list(project="atlas")} == {a}
    assert {i["item_id"] for i in store.personal.list(unscoped=True)} == {free}
    assert {i["item_id"] for i in store.personal.list()} == {a, b, free}
    store.personal.update(a, clear_project=True)
    assert store.personal.get(a)["project"] is None


# ------------------------------------------------------------------ privacy and audit


def test_audit_never_contains_item_text(store):
    iid = task(store, "Secret plan details", details="very private body", private=True)
    store.personal.update(iid, title="Renamed secret plan")
    store.personal.delete(iid)
    entries = store.audit.list(target_type="personal_item", target_id=iid)
    assert [e["action"] for e in entries].count("personal_item_deleted") == 1
    dump = str(entries)
    for text in ("Secret plan", "very private", "Renamed"):
        assert text not in dump


def test_delete_removes_the_content_everywhere_in_the_database(store):
    iid = task(store, "Distinctive zebra errand", details="zebra zebra zebra")
    assert store.personal.delete(iid) is True
    assert store.personal.delete(iid) is False
    db = sqlite3.connect(store.database_path)
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name.startswith("sqlite_"):
                continue
            blob = str(db.execute(f'SELECT * FROM "{name}"').fetchall())  # noqa: S608
            assert "zebra" not in blob, name
    finally:
        db.close()


def test_personal_items_never_enter_model_context(store):
    task(store, "Distinctive quokka errand", details="quokka quokka")
    store.ensure_conversation("conv-p-1")
    store.append_message("conv-p-1", "user", "remind me about the quokka errand")
    packet = assemble_memory_context(store, "what is the quokka errand", "conv-p-1")
    assert "quokka" not in str(packet.message) and packet.empty


# ------------------------------------------------------------------ retention and export


def test_retention_previews_then_purges_only_finished_old_items(store):
    old_done = task(store, "old done")
    old_cancelled = task(store, "old cancelled")
    old_open = task(store, "old open")
    fresh_done = task(store, "fresh done")
    store.personal.update(old_done, status="done")
    store.personal.update(old_cancelled, status="cancelled")
    store.personal.update(fresh_done, status="done")
    for iid in (old_done, old_cancelled, old_open):
        backdate(store, iid, 120)
    preview = store.personal.purge_finished(30)
    assert preview["dry_run"] and preview["count"] == 2
    assert len(store.personal.list()) == 4  # nothing removed by a preview
    applied = store.personal.purge_finished(30, dry_run=False)
    assert applied["count"] == 2
    assert {i["item_id"] for i in store.personal.list()} == {old_open, fresh_done}
    for bad in (0, -1, 40000, True):
        with pytest.raises(ValueError):
            store.personal.purge_finished(bad)


def test_export_includes_every_item_with_provenance_and_flags(store):
    a = task(store, "a", project="Atlas", private=True)
    b = task(store, "b")
    store.personal.update(b, status="done")
    exported = store.export_data()["personal_items"]
    by_id = {i["item_id"]: i for i in exported}
    assert set(by_id) == {a, b}
    assert by_id[a]["private"] is True and by_id[a]["project"] == "Atlas"
    assert by_id[b]["status"] == "done" and by_id[b]["provenance"] == "owner_provided"


def test_upgrade_from_v5_database_keeps_data_and_adds_the_table(tmp_path):
    path = tmp_path / "old.sqlite3"
    first = MemoryStore(path)
    rid, _ = first.structured.add(record_type="fact", title="Old fact", body="Kept after upgrade.")
    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE personal_items")
        db.execute("DELETE FROM schema_migrations WHERE version = 6")
        db.commit()
    finally:
        db.close()
    reopened = MemoryStore(path)
    assert reopened.structured.get(rid)["title"] == "Old fact"
    assert reopened.personal.get(task(reopened)) is not None


# ------------------------------------------------------------------ API


def test_api_crud_filters_retention_and_export(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    created = client.post("/api/personal/items", json={
        "kind": "task", "title": "Write report", "project": "Atlas", "private": True})
    assert created.status_code == 201
    iid = created.json()["item_id"]

    assert client.post("/api/personal/items", json={"kind": "reminder", "title": "x"}
                       ).status_code == 400
    assert client.post("/api/personal/items", json={"kind": "task", "title": "x", "bogus": 1}
                       ).status_code == 422
    assert client.get("/api/personal/items/missing").status_code == 404

    patched = client.patch(f"/api/personal/items/{iid}", json={"status": "done"}).json()
    assert patched["changed"] == ["status"] and patched["item"]["status"] == "done"
    assert client.patch(f"/api/personal/items/{iid}", json={}).status_code == 400
    assert client.patch("/api/personal/items/missing", json={"status": "done"}).status_code == 404

    listed = client.get("/api/personal/items", params={"project": "atlas"}).json()
    assert [i["item_id"] for i in listed["items"]] == [iid] and listed["counts"] == {"task": 1}

    backdate(memory, iid, 90)
    preview = client.post("/api/personal/items/retention", json={"older_than_days": 30}).json()
    assert preview["dry_run"] and preview["count"] == 1
    assert client.post("/api/personal/items/retention", json={"older_than_days": 0}
                       ).status_code == 400
    assert client.get("/api/memory/export").json()["personal_items"][0]["item_id"] == iid
    applied = client.post("/api/personal/items/retention",
                          json={"older_than_days": 30, "dry_run": False}).json()
    assert applied["count"] == 1
    assert client.delete(f"/api/personal/items/{iid}").status_code == 404
