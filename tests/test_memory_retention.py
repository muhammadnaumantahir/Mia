import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def age(store, table, key_col, key, days):
    stamp = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    db = sqlite3.connect(store.database_path)
    db.execute(f"UPDATE {table} SET updated_at=? WHERE {key_col}=?", (stamp, key))
    db.commit()
    db.close()


def seed(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite3")
    old, new = store.ensure_conversation(), store.ensure_conversation()
    store.append_message(old, "user", "old chat")
    store.append_message(new, "user", "new chat")
    old_item = store.add_item(kind="note", title="Old note", content="alpha obsolete")
    new_item = store.add_item(kind="note", title="New note", content="alpha current")
    age(store, "conversations", "conversation_id", old, 100)
    age(store, "memory_items", "item_id", old_item, 100)
    return store, old, new, old_item, new_item


def test_dry_run_reports_but_deletes_nothing(tmp_path):
    store, old, _, old_item, _ = seed(tmp_path)
    preview = store.purge_older_than(30)
    assert preview["dry_run"] is True
    assert preview["conversations"]["count"] == 1 and preview["items"]["count"] == 1
    assert store.get_conversation(old) is not None and store.get_item(old_item) is not None


def test_purge_removes_only_old_data_including_search_index(tmp_path):
    store, old, new, old_item, new_item = seed(tmp_path)
    result = store.purge_older_than(30, dry_run=False)
    assert result["conversations"]["count"] == 1
    assert store.get_conversation(old) is None and store.get_conversation(new) is not None
    assert store.get_item(old_item) is None and store.get_item(new_item) is not None
    assert [hit["item_id"] for hit in store.search("alpha")] == [new_item]
    db = sqlite3.connect(store.database_path)
    assert db.execute("SELECT COUNT(*) FROM conversation_messages WHERE conversation_id=?",
                      (old,)).fetchone()[0] == 0
    db.close()


def test_scope_flags_are_respected(tmp_path):
    store, old, _, old_item, _ = seed(tmp_path)
    store.purge_older_than(30, conversations=False, dry_run=False)
    assert store.get_conversation(old) is not None and store.get_item(old_item) is None


@pytest.mark.parametrize("bad", [0, -5, True, "7", 1.5])
def test_invalid_days_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        MemoryStore(tmp_path / "m.sqlite3").purge_older_than(bad)


def test_api_defaults_to_preview_and_audits_real_purge(tmp_path):
    store, old, _, old_item, _ = seed(tmp_path)
    obs = ObservatoryStore(tmp_path / "o.sqlite3")
    client = TestClient(create_app(obs, memory=store))
    preview = client.post("/api/memory/purge", json={"older_than_days": 30}).json()
    assert preview["dry_run"] is True and store.get_item(old_item) is not None
    assert client.post("/api/memory/purge", json={"older_than_days": 0}).status_code == 422
    done = client.post("/api/memory/purge", json={"older_than_days": 30, "dry_run": False}).json()
    assert done["items"]["count"] == 1 and store.get_item(old_item) is None
    assert any(e["event_type"] == "retention_purge" for e in obs.list_events())
