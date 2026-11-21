"""Owner-controlled retention for retired structured-memory records (MIA-BRAIN-04).

Policy: only *retired* records (superseded, outdated, archived, rejected, expired) older
than the configured period can be purged. Live records (active, completed) and unreviewed
suggestions (pending, which have their own expiry) are never touched. Nothing is deleted
automatically: the owner previews first, then applies.
"""

from datetime import UTC, datetime, timedelta

import pytest

from mia.memory.store import MemoryStore

from .test_brain_pipeline import Scripted, client_for


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def backdate(store, record_id, days):
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with store._connection() as db:
        db.execute(
            "UPDATE structured_memory SET updated_at = ? WHERE record_id = ?", (when, record_id)
        )


def add(store, title, **kw):
    record_id, _ = store.structured.add(
        record_type="decision", title=title, body=f"Body of {title}.", **kw
    )
    return record_id


def make_world(store):
    """One record in every status, each 120 days old, plus a fresh retired record."""
    ids = {}
    ids["active"] = add(store, "Live decision")
    ids["completed"] = add(store, "Finished decision")
    store.structured.set_status(ids["completed"], "completed")
    ids["pending"] = add(store, "Unreviewed suggestion", status="pending")
    ids["superseded"] = add(store, "Old wording")
    ids["successor"], _ = store.structured.supersede(ids["superseded"], body="New wording.")
    ids["rejected"] = add(store, "Rejected idea")
    store.structured.set_status(ids["rejected"], "rejected")
    ids["archived"] = add(store, "Archived note")
    store.structured.archive(ids["archived"])
    ids["outdated"] = add(store, "Outdated note")
    store.structured.mark_outdated(ids["outdated"])
    ids["fresh_retired"] = add(store, "Recently archived")
    store.structured.archive(ids["fresh_retired"])
    for key, record_id in ids.items():
        if key != "fresh_retired":
            backdate(store, record_id, 120)
    return ids


RETIRED = {"superseded", "rejected", "archived", "outdated"}


def exists(store, record_id):
    return store.structured.get(record_id) is not None


# ------------------------------------------------------------------ setting


def test_setting_defaults_to_keep_forever_and_is_validated(store):
    assert store.settings.get("structured_retention_days") is None
    store.settings.update({"structured_retention_days": 90})
    assert store.settings.get("structured_retention_days") == 90
    store.settings.update({"structured_retention_days": None})
    assert store.settings.get("structured_retention_days") is None
    for bad in (0, -5, 36501, True, "90", 1.5):
        with pytest.raises(ValueError):
            store.settings.update({"structured_retention_days": bad})


# ------------------------------------------------------------------ purge


def test_preview_lists_only_old_retired_records_and_deletes_nothing(store):
    ids = make_world(store)
    result = store.purge_retired_records(30)

    assert result["dry_run"] is True
    assert {r["id"] for r in result["sample"]} == {ids[k] for k in RETIRED}
    assert result["count"] == len(RETIRED)
    assert all(exists(store, record_id) for record_id in ids.values())


def test_apply_deletes_retired_records_but_never_live_or_pending_ones(store):
    ids = make_world(store)
    result = store.purge_retired_records(30, dry_run=False)

    assert result["dry_run"] is False and result["count"] == len(RETIRED)
    for key in RETIRED:
        assert not exists(store, ids[key]), key
    for key in ("active", "completed", "pending", "successor", "fresh_retired"):
        assert exists(store, ids[key]), key


def test_purging_a_superseded_predecessor_leaves_the_live_successor_intact(store):
    ids = make_world(store)
    store.purge_retired_records(30, dry_run=False)

    successor = store.structured.get(ids["successor"])
    assert successor["status"] == "active" and successor["body"] == "New wording."
    assert successor["supersedes_id"] is None  # no dangling pointer to the deleted record


def test_retired_records_leave_no_trace_in_search_or_embedding_cache(store):
    ids = make_world(store)
    store.purge_retired_records(30, dry_run=False)
    with store._connection() as db:
        left = db.execute(
            "SELECT COUNT(*) FROM structured_fts WHERE record_id = ?", (ids["archived"],)
        ).fetchone()[0]
        cache = db.execute("SELECT COUNT(*) FROM memory_embedding_cache").fetchone()[0]
    assert left == 0 and cache == 0


def test_age_boundary_is_respected(store):
    young = add(store, "Archived forty days ago")
    store.structured.archive(young)
    backdate(store, young, 40)
    old = add(store, "Archived ninety days ago")
    store.structured.archive(old)
    backdate(store, old, 90)

    store.purge_retired_records(60, dry_run=False)
    assert exists(store, young) and not exists(store, old)


def test_uses_the_configured_period_when_none_is_given(store):
    ids = make_world(store)
    with pytest.raises(ValueError, match="retention"):
        store.purge_retired_records()  # nothing configured: refuse rather than guess
    store.settings.update({"structured_retention_days": 30})
    assert store.purge_retired_records()["count"] == len(RETIRED)
    assert exists(store, ids["archived"])  # it was only a preview


def test_invalid_period_is_rejected(store):
    for bad in (0, -1, True, "30"):
        with pytest.raises(ValueError):
            store.purge_retired_records(bad)


def test_purge_is_audited_without_storing_the_deleted_text(store):
    ids = make_world(store)
    store.purge_retired_records(30, dry_run=False, actor="retention")
    with store._connection() as db:
        rows = db.execute(
            "SELECT actor, detail_json FROM memory_audit_log WHERE action = 'record_deleted'"
        ).fetchall()
    assert len(rows) == len(RETIRED)
    assert {r["actor"] for r in rows} == {"retention"}
    blob = " ".join(r["detail_json"] for r in rows)
    assert "Old wording" not in blob and "Rejected idea" not in blob
    assert ids["archived"] in blob


# ------------------------------------------------------------------ API


def test_retention_api_previews_then_applies_and_validates(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch, Scripted(reply="ok"))
    ids = make_world(memory)

    assert client.post("/api/memory/records/retention", json={}).status_code == 400  # no period

    preview = client.post("/api/memory/records/retention", json={"older_than_days": 30}).json()
    assert preview["dry_run"] is True and preview["count"] == len(RETIRED)
    assert exists(memory, ids["archived"])

    applied = client.post(
        "/api/memory/records/retention", json={"older_than_days": 30, "dry_run": False}
    ).json()
    assert applied["dry_run"] is False and applied["count"] == len(RETIRED)
    assert not exists(memory, ids["archived"]) and exists(memory, ids["active"])

    assert client.post(
        "/api/memory/records/retention", json={"older_than_days": 0}
    ).status_code in {400, 422}


def test_retention_period_can_be_set_and_cleared_through_settings_api(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch, Scripted(reply="ok"))
    r = client.put("/api/memory/settings", json={"structured_retention_days": 90})
    assert r.status_code == 200 and r.json()["structured_retention_days"] == 90
    r = client.put("/api/memory/settings", json={"structured_retention_days": None})
    assert r.status_code == 200 and r.json()["structured_retention_days"] is None
    assert client.put(
        "/api/memory/settings", json={"structured_retention_days": 0}
    ).status_code == 400
