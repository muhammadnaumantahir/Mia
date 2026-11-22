"""Unified task manager on personal items (MIA-LIFE-02)."""

import sqlite3
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mia.memory.personal import DuplicateItemError, next_occurrence
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for

NY = ZoneInfo("America/New_York")


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def task(store, title="Pay rent", **kw):
    return store.personal.add(kind="task", title=title, **kw)


def utc(text):
    return datetime.fromisoformat(text).astimezone(UTC)


# ------------------------------------------------------------------ priority / validation


def test_priority_default_validation_and_filter(store):
    a = task(store, "a")
    b = task(store, "b", priority=1)
    assert store.personal.get(a)["priority"] == 2
    for bad in (0, 4, True, "high"):
        with pytest.raises(ValueError):
            task(store, "c", priority=bad)
    assert [i["item_id"] for i in store.personal.list(priority=1)] == [b]
    assert store.personal.update(a, priority=3) == ["priority"]
    assert store.personal.get(a)["priority"] == 3


def test_recurrence_validation(store):
    with pytest.raises(ValueError):
        task(store, "r", recurrence={"freq": "daily", "interval": 1})  # needs a due time
    due = "2026-12-01T09:00:00Z"
    for bad in ({"freq": "hourly"}, {"freq": "daily", "interval": 0},
                {"freq": "daily", "interval": 366}, {"freq": "daily", "x": 1},
                {"freq": "daily", "interval": True}, "daily"):
        with pytest.raises(ValueError):
            task(store, "r", due_at=due, recurrence=bad)
    iid = task(store, "r", due_at=due, recurrence={"freq": "weekly"})
    assert store.personal.get(iid)["recurrence"] == {"freq": "weekly", "interval": 1}
    with pytest.raises(ValueError):  # cannot remove the due time of a recurring task
        store.personal.update(iid, clear_due=True)
    assert store.personal.update(iid, clear_recurrence=True) == ["recurrence"]
    assert store.personal.get(iid)["recurrence"] is None


# ------------------------------------------------------------------ duplicates


def test_duplicate_open_items_are_refused_with_the_existing_id(store):
    first = task(store, "Pay  Rent", project="Home")
    with pytest.raises(DuplicateItemError) as err:
        task(store, "pay rent", project="home")
    assert err.value.existing_id == first
    task(store, "Pay rent", project="Work")  # other project: fine
    task(store, "Pay rent")  # unscoped: fine
    assert task(store, "pay rent", project="Home", allow_duplicate=True)
    store.personal.update(first, status="done")  # finished items do not block
    assert task(store, "Pay rent", project="Home", allow_duplicate=True)


def test_rename_and_reopen_respect_duplicates(store):
    a = task(store, "Alpha")
    b = task(store, "Beta")
    with pytest.raises(DuplicateItemError):
        store.personal.update(b, title="alpha")
    store.personal.update(a, status="done")
    store.personal.update(b, title="alpha")  # fine: the open Alpha is gone
    with pytest.raises(DuplicateItemError):
        store.personal.update(a, status="open")  # reopening would duplicate b


# ------------------------------------------------------------------ recurrence math


def test_daily_keeps_wall_clock_time_across_dst():
    # US spring-forward: 2026-03-08. 09:00 New York stays 09:00 local.
    due = datetime(2026, 3, 7, 9, 0, tzinfo=NY).astimezone(UTC).isoformat()
    nxt = utc(next_occurrence(due, {"freq": "daily", "interval": 1}, NY,
                              datetime(2026, 3, 7, 12, tzinfo=UTC)))
    local = nxt.astimezone(NY)
    assert (local.day, local.hour, local.minute) == (8, 9, 0)
    assert nxt - utc(due) == timedelta(hours=23)  # not a fixed 24h step


def test_monthly_clamps_without_drifting():
    due = datetime(2026, 1, 31, 8, 0, tzinfo=UTC).isoformat()
    rule = {"freq": "monthly", "interval": 1}
    far_past = datetime(2020, 1, 1, tzinfo=UTC)
    feb = utc(next_occurrence(due, rule, ZoneInfo("UTC"), far_past))
    assert (feb.month, feb.day) == (2, 28)
    # two steps from the ORIGINAL date: March 31st again, not March 28th
    mar = utc(next_occurrence(due, rule, ZoneInfo("UTC"), datetime(2026, 3, 1, tzinfo=UTC)))
    assert (mar.month, mar.day) == (3, 31)


def test_overdue_recurring_task_skips_missed_occurrences():
    due = datetime(2026, 1, 1, 9, 0, tzinfo=UTC).isoformat()
    now = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)
    nxt = utc(next_occurrence(due, {"freq": "daily", "interval": 1}, ZoneInfo("UTC"), now))
    assert nxt == datetime(2026, 1, 11, 9, 0, tzinfo=UTC)


def test_weekly_interval():
    due = datetime(2030, 1, 7, 9, 0, tzinfo=UTC).isoformat()
    nxt = utc(next_occurrence(due, {"freq": "weekly", "interval": 2}, ZoneInfo("UTC"),
                              datetime(2026, 1, 1, tzinfo=UTC)))
    assert nxt == datetime(2030, 1, 21, 9, 0, tzinfo=UTC)


# ------------------------------------------------------------------ completion


def test_complete_recurring_creates_next_occurrence_once(store):
    store.settings.update({"owner_timezone": "America/New_York"})
    due = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0).isoformat()
    iid = task(store, "Water plants", due_at=due, priority=1, project="Home",
               recurrence={"freq": "daily"})
    outcome = store.personal.complete(iid)
    nxt = store.personal.get(outcome["next_item_id"])
    assert outcome["changed"] == ["status"]
    assert nxt["status"] == "open" and nxt["priority"] == 1 and nxt["project"] == "Home"
    assert nxt["recurrence"] == {"freq": "daily", "interval": 1}
    assert utc(nxt["due_at"]) > utc(due)
    assert store.personal.get(iid)["status"] == "done"
    again = store.personal.complete(iid)  # idempotent: no second copy
    assert again == {"changed": [], "next_item_id": None}
    assert len(store.personal.list(status="open")) == 1


def test_complete_plain_task_and_status_route_for_recurring(store):
    plain = task(store, "One-off")
    assert store.personal.complete(plain)["next_item_id"] is None
    rec = task(store, "Weekly", due_at="2030-01-01T09:00:00Z", recurrence={"freq": "weekly"})
    with pytest.raises(ValueError):
        store.personal.update(rec, status="done")  # must use complete()
    with pytest.raises(Exception):
        store.personal.complete("missing")


# ------------------------------------------------------------------ history / timezone setting


def test_history_records_structural_changes_without_text(store):
    iid = task(store, "Private title text", details="private details text")
    store.personal.update(iid, priority=1, title="Another private title", project="Atlas")
    store.personal.complete(iid)
    history = store.personal.history(iid)
    fields = [h["field"] for h in history]
    assert {"priority", "project", "title", "status"} <= set(fields)
    prio = next(h for h in history if h["field"] == "priority")
    assert (prio["old_value"], prio["new_value"]) == ("2", "1")
    for text in ("Private title", "Another private", "private details"):
        assert text not in str(history)
    store.personal.delete(iid)
    assert store.personal.history(iid) == []  # deleted with the item


def test_owner_timezone_setting_validates(store):
    assert store.settings.get("owner_timezone") == "UTC"
    assert store.settings.update({"owner_timezone": "Asia/Karachi"})["owner_timezone"] == "Asia/Karachi"
    for bad in ("Mars/Olympus", "", 5, "x" * 65):
        with pytest.raises(ValueError):
            store.settings.update({"owner_timezone": bad})


def test_overdue_and_due_before_filters(store):
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    soon = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    late = task(store, "late", due_at=past)
    ok = task(store, "soon", due_at=soon)
    done = task(store, "done late", due_at=past)
    store.personal.update(done, status="done")
    assert [i["item_id"] for i in store.personal.list(overdue=True)] == [late]
    horizon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert {i["item_id"] for i in store.personal.list(due_before=horizon)} == {late, ok, done}
    with pytest.raises(ValueError):
        store.personal.list(due_before="2026-12-01")  # naive time refused


# ------------------------------------------------------------------ migration / export


def test_upgrade_from_v6_database_keeps_items(tmp_path):
    path = tmp_path / "old.sqlite3"
    first = MemoryStore(path)
    iid = task(first, "Survives upgrade")
    db = sqlite3.connect(path)
    try:
        db.execute("DROP INDEX IF EXISTS idx_personal_dedupe")
        for column in ("priority", "recurrence", "series_id", "dedupe_key"):
            db.execute(f"ALTER TABLE personal_items DROP COLUMN {column}")  # noqa: S608
        db.execute("DROP TABLE personal_item_history")
        db.execute("DELETE FROM schema_migrations WHERE version = 7")
        db.commit()
    finally:
        db.close()
    reopened = MemoryStore(path)
    item = reopened.personal.get(iid)
    assert item["title"] == "Survives upgrade" and item["priority"] == 2
    assert reopened.personal.update(iid, priority=1) == ["priority"]
    # old rows have no duplicate key, so they never block; new rows do
    new = task(reopened, "Another")
    with pytest.raises(DuplicateItemError):
        task(reopened, "Another")
    assert new


def test_export_includes_history_and_recurrence(store):
    iid = task(store, "x", due_at="2030-01-01T09:00:00Z", recurrence={"freq": "daily"})
    store.personal.complete(iid)
    data = store.export_data()
    assert any(i["recurrence"] for i in data["personal_items"])
    assert data["personal_item_history"][0]["item_id"] == iid


# ------------------------------------------------------------------ API


def test_api_duplicates_complete_history_and_timezone(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    body = {"kind": "task", "title": "Pay rent", "due_at": "2030-01-01T09:00:00+05:00",
            "priority": 1, "recurrence": {"freq": "monthly", "interval": 1}}
    first = client.post("/api/personal/items", json=body)
    assert first.status_code == 201
    iid = first.json()["item_id"]
    dup = client.post("/api/personal/items", json=body)
    assert dup.status_code == 409 and dup.json()["detail"]["existing_item_id"] == iid
    assert client.post("/api/personal/items", json={**body, "allow_duplicate": True}
                       ).status_code == 201
    assert client.post("/api/personal/items", json={**body, "title": "x", "priority": 9}
                       ).status_code == 400

    blocked = client.patch(f"/api/personal/items/{iid}", json={"status": "done"})
    assert blocked.status_code == 400
    done = client.post(f"/api/personal/items/{iid}/complete").json()
    assert done["next_item_id"] and done["item"]["status"] == "done"
    assert client.post("/api/personal/items/missing/complete").status_code == 404

    hist = client.get(f"/api/personal/items/{iid}/history").json()["history"]
    assert [h["field"] for h in hist] == ["status"]
    assert client.get("/api/personal/items/missing/history").status_code == 404
    assert client.get("/api/personal/items", params={"priority": 1, "status": "open"}
                      ).json()["items"]

    ok = client.put("/api/memory/settings", json={"owner_timezone": "Asia/Karachi"})
    assert ok.status_code == 200 and memory.settings.get("owner_timezone") == "Asia/Karachi"
    bad = client.put("/api/memory/settings", json={"owner_timezone": "Mars/Olympus"})
    assert bad.status_code == 400
