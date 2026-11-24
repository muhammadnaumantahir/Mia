"""MIA-LIFE-09 x MIA-LIFE-02: due tasks and reminders become notifications."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.life.notification_api import register_notification_routes  # noqa: F401  (import check)
from mia.life.notifications import NotificationCenter
from mia.life.task_notifications import sync_due_tasks
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))



def iso(moment):
    return moment.isoformat()


@pytest.fixture()
def env(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    center = NotificationCenter(tmp_path / "n.sqlite3")
    return memory, center


def add(memory, title="Pay rent", due=None, kind="task", **kw):
    return memory.personal.add(kind=kind, title=title, due_at=iso(due) if due else None, **kw)


def titles(center, now=NOW):
    return [i["title"] for i in center.list(now=now)]


def test_due_soon_task_becomes_one_notification_with_reason_source_and_evidence(env):
    memory, center = env
    center.update_settings({"timezone": "Asia/Karachi"})
    item = add(memory, due=NOW + timedelta(hours=5))  # 22:00 in Karachi
    assert sync_due_tasks(memory.personal, center, now=NOW) == {"submitted": 1, "withdrawn": 0}
    info = center.explain(center.list(now=NOW)[0]["notification_id"], now=NOW)
    assert "22:00" in info["why"] and "Asia/Karachi" in info["why"]
    assert info["source"] == {"type": "task", "ref": item}
    assert info["evidence"] == [{"type": "personal_item", "ref": item}]
    assert titles(center) == ["Task due soon: Pay rent"]


def test_far_future_done_cancelled_and_undated_items_are_ignored(env):
    memory, center = env
    add(memory, "Later", due=NOW + timedelta(days=5))
    add(memory, "No date")
    done = add(memory, "Done one", due=NOW + timedelta(hours=2))
    memory.personal.complete(done)
    note = add(memory, "A note", kind="note", due=NOW + timedelta(hours=2))
    assert note
    assert sync_due_tasks(memory.personal, center, now=NOW)["submitted"] == 0
    assert center.list(now=NOW) == []


def test_repeated_sync_does_not_repeat_the_notification(env):
    memory, center = env
    add(memory, due=NOW + timedelta(hours=3))
    for minutes in (0, 10, 20):
        sync_due_tasks(memory.personal, center, now=NOW + timedelta(minutes=minutes))
    items = center.list(now=NOW + timedelta(minutes=20))
    assert len(items) == 1 and items[0]["occurrences"] == 3


def test_overdue_high_priority_is_urgent_and_bypasses_quiet_hours(env):
    memory, center = env
    center.update_settings({"quiet_hours_enabled": True, "quiet_start_minute": 22 * 60, "quiet_end_minute": 7 * 60})
    night = datetime(2026, 6, 1, 23, 30, tzinfo=UTC)
    add(memory, "Call the bank", due=night - timedelta(hours=1), priority=1)
    add(memory, "Water plants", due=night - timedelta(hours=1), priority=2)
    sync_due_tasks(memory.personal, center, now=night)
    shown = center.list(now=night)
    assert [i["title"] for i in shown] == ["Task overdue: Call the bank"] and shown[0]["priority"] == "urgent"
    assert center.counts(night)["held"] == 1  # the normal one waits for morning
    assert "Task overdue: Water plants" in titles(center, datetime(2026, 6, 2, 7, 0, tzinfo=UTC))


def test_reminders_are_labelled_as_reminders(env):
    memory, center = env
    add(memory, "Take the parcel", kind="reminder", due=NOW + timedelta(hours=1))
    sync_due_tasks(memory.personal, center, now=NOW)
    assert titles(center) == ["Reminder due soon: Take the parcel"]


def test_private_items_never_leak_title_or_details(env):
    memory, center = env
    add(memory, "Secret surgery appointment", details="ward 4", due=NOW + timedelta(hours=2), private=True)
    sync_due_tasks(memory.personal, center, now=NOW)
    item = center.list(now=NOW)[0]
    blob = " ".join(str(v) for v in item.values())
    assert "surgery" not in blob.lower() and "ward" not in blob and item["title"] == "Private task due soon"


def test_completing_or_deleting_a_task_withdraws_its_notification_without_a_cooldown(env):
    memory, center = env
    a = add(memory, "A", due=NOW + timedelta(hours=2))
    b = add(memory, "B", due=NOW + timedelta(hours=2))
    sync_due_tasks(memory.personal, center, now=NOW)
    assert len(center.list(now=NOW)) == 2
    memory.personal.complete(a)
    memory.personal.delete(b)
    assert sync_due_tasks(memory.personal, center, now=NOW) == {"submitted": 0, "withdrawn": 2}
    assert center.list(now=NOW) == []
    assert center.counts(NOW) == {"expired": 2}


def test_changing_the_due_time_replaces_the_old_notification(env):
    memory, center = env
    item = add(memory, "Move me", due=NOW + timedelta(hours=2))
    sync_due_tasks(memory.personal, center, now=NOW)
    memory.personal.update(item, due_at=iso(NOW + timedelta(hours=6)))
    result = sync_due_tasks(memory.personal, center, now=NOW)
    assert result == {"submitted": 1, "withdrawn": 1}
    assert len(center.list(now=NOW)) == 1


def test_recurring_task_notifies_again_for_its_next_occurrence(env):
    memory, center = env
    real = datetime.now(UTC)  # PersonalData.complete() advances from the real clock
    item = add(memory, "Daily standup", due=real + timedelta(hours=1), recurrence={"freq": "daily", "interval": 1})
    sync_due_tasks(memory.personal, center, now=real)
    first = center.list(now=real)[0]["notification_id"]
    memory.personal.complete(item)
    later = real + timedelta(hours=26)
    result = sync_due_tasks(memory.personal, center, now=later)
    assert result["submitted"] == 1
    assert first not in [i["notification_id"] for i in center.list(now=later)]


def test_list_endpoint_runs_producers_and_survives_a_failing_one(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    center = NotificationCenter(tmp_path / "n.sqlite3")
    add(memory, "Soon", due=datetime.now(UTC) + timedelta(hours=2))
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), memory=memory, notifications=center)
    client = TestClient(app)
    assert [n["title"] for n in client.get("/api/notifications").json()["notifications"]] == ["Task due soon: Soon"]

    from fastapi import FastAPI

    broken = FastAPI()
    register_notification_routes(broken, center, producers=[lambda: 1 / 0])
    assert TestClient(broken).get("/api/notifications").status_code == 200
