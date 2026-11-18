import sqlite3
from datetime import UTC, datetime, timedelta

from mia.agent.tasks import TaskStore


def approved_task(store, title="t"):
    task_id = store.create_task(title=title, goal="goal text", source="interactive")
    store.transition(task_id, "approved", reason="owner approved", actor="local-user")
    return task_id


def set_lease(store, task_id, owner, delta_seconds):
    expiry = (datetime.now(UTC) + timedelta(seconds=delta_seconds)).isoformat()
    db = sqlite3.connect(store.database_path)
    db.execute(
        "UPDATE tasks SET lease_owner=?, lease_expires_at=? WHERE task_id=?",
        (owner, expiry, task_id),
    )
    db.commit()
    db.close()


def test_claim_sets_lease_and_leaving_running_clears_it(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    task_id = approved_task(store)
    claimed = store.claim_task(task_id)
    assert claimed["lease_owner"] == store.lease_owner and claimed["lease_expires_at"]
    done = store.transition(task_id, "completed", reason="done")
    assert done["lease_owner"] is None and done["lease_expires_at"] is None


def test_renew_extends_only_own_running_lease(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    task_id = approved_task(store)
    first = store.claim_task(task_id)["lease_expires_at"]
    assert store.renew_lease(task_id) is True
    assert store.get_task(task_id)["lease_expires_at"] >= first
    set_lease(store, task_id, "99999:other", 600)
    assert store.renew_lease(task_id) is False  # lease is held by someone else
    store.transition(task_id, "cancelled", reason="stop")
    assert store.renew_lease(task_id) is False  # not running any more


def test_startup_recovery_skips_task_leased_by_another_live_process(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    task_id = approved_task(store)
    store.claim_task(task_id)
    set_lease(store, task_id, "424242:abc", 600)  # different pid, unexpired
    assert store.recover_interrupted_tasks() == []
    assert store.get_task(task_id)["status"] == "running"
    set_lease(store, task_id, "424242:abc", -5)  # expired
    assert store.recover_interrupted_tasks() == [task_id]
    assert store.get_task(task_id)["last_error"] == "interrupted_on_restart"


def test_startup_recovery_handles_legacy_row_without_lease(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    task_id = approved_task(store)
    store.claim_task(task_id)
    db = sqlite3.connect(store.database_path)
    db.execute("UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL")
    db.commit()
    db.close()
    assert store.recover_interrupted_tasks() == [task_id]


def test_expired_lease_recovery_never_reaps_own_or_live_work(tmp_path):
    store = TaskStore(tmp_path / "t.sqlite3")
    mine, live, dead, legacy = (approved_task(store, name) for name in "abcd")
    for task_id in (mine, live, dead, legacy):
        store.claim_task(task_id)
    set_lease(store, mine, store.lease_owner, -60)  # own long step: leave it
    set_lease(store, live, "424242:x", 600)
    set_lease(store, dead, "424242:y", -60)
    db = sqlite3.connect(store.database_path)
    db.execute(
        "UPDATE tasks SET lease_owner=NULL, lease_expires_at=NULL WHERE task_id=?", (legacy,)
    )
    db.commit()
    db.close()
    assert store.recover_expired_leases() == [dead]
    statuses = {t: store.get_task(t)["status"] for t in (mine, live, dead, legacy)}
    assert statuses == {mine: "running", live: "running", dead: "failed", legacy: "running"}
    event = store.list_events(dead)[-1]
    assert event["actor"] == "lease-recovery"
    assert store.get_task(dead)["last_error"] == "lease_expired"


def test_existing_database_without_lease_columns_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    first = TaskStore(path)
    db = sqlite3.connect(path)
    db.execute("ALTER TABLE tasks DROP COLUMN lease_owner")
    db.execute("ALTER TABLE tasks DROP COLUMN lease_expires_at")
    db.commit()
    db.close()
    migrated = TaskStore(path)
    task_id = approved_task(migrated)
    assert migrated.claim_task(task_id)["lease_owner"] == migrated.lease_owner
    assert first.lease_owner != migrated.lease_owner
