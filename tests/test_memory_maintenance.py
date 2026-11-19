import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.capture import CaptureService
from mia.memory.maintenance import MaintenanceService
from mia.memory.store import MemoryStore
from mia.memory.structured import RecordStateError
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def make(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    return store, MaintenanceService(store)


def add(store, record_type, title, body, **kw):
    return store.structured.add(record_type=record_type, title=title, body=body, **kw)[0]


def iso(**delta):
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def backdate(store, record_id, **columns):
    db = sqlite3.connect(store.database_path)
    for column, value in columns.items():
        db.execute(f"UPDATE structured_memory SET {column} = ? WHERE record_id = ?", (value, record_id))
    db.commit()
    db.close()


# --------------------------------------------------------------------- expiry


def test_dry_run_reports_expiry_but_changes_nothing_and_apply_expires(tmp_path):
    store, service = make(tmp_path)
    temp = add(store, "fact", "Temp", "A short lived memory", temporary=True, expires_at=iso(days=1))
    stale = add(store, "fact", "Stale", "An unreviewed suggestion text", status="pending",
                provenance="observed")
    fresh = add(store, "fact", "Fresh", "A fresh unreviewed suggestion", status="pending",
                provenance="observed")
    backdate(store, temp, expires_at=iso(hours=-1))
    backdate(store, stale, created_at=iso(days=-40))

    dry = service.run(apply=False)
    assert dry["applied"] is False
    assert dry["expired_temporary"] == [temp] and dry["expired_pending"] == [stale]
    assert store.structured.get(temp)["status"] == "active"
    assert store.structured.get(stale)["status"] == "pending"

    done = service.run(apply=True)
    assert done["expired_temporary"] == [temp] and done["expired_pending"] == [stale]
    assert store.structured.get(temp)["status"] == "expired"
    assert store.structured.get(stale)["status"] == "expired"
    assert store.structured.get(fresh)["status"] == "pending"  # inside the window
    again = service.run(apply=True)
    assert again["expired_temporary"] == [] and again["expired_pending"] == []


def test_pending_window_follows_the_owner_setting(tmp_path):
    store, service = make(tmp_path)
    pending = add(store, "fact", "Old", "A reasonably old suggestion", status="pending",
                  provenance="observed")
    backdate(store, pending, created_at=iso(days=-10))
    assert service.run()["expired_pending"] == []
    store.settings.update({"pending_retention_days": 7})
    assert service.run()["expired_pending"] == [pending]


# ----------------------------------------------------------------- duplicates


def test_duplicates_are_clustered_with_a_suggested_keep_and_nothing_is_changed(tmp_path):
    store, service = make(tmp_path)
    inferred = add(store, "fact", "Editor A", "The owner edits code with Neovim every day",
                   provenance="inferred", evidence=[{"type": "message", "ref": "m1"}])
    stated = add(store, "fact", "Editor B", "The owner edits code with Neovim every day.")
    add(store, "fact", "Unrelated", "The server rack is on the third floor")
    report = service.run()
    assert len(report["duplicates"]) == 1
    cluster = report["duplicates"][0]
    assert cluster["suggested_keep"] == stated  # owner-provided beats inferred
    assert cluster["duplicates"] == [inferred]
    assert store.structured.get(inferred)["status"] == "active"  # report only


def test_consolidate_merges_without_deleting_and_leaves_retrieval(tmp_path):
    store, service = make(tmp_path)
    keep = add(store, "fact", "Editor", "The owner edits code with Neovim every day")
    dup = add(store, "fact", "Editor copy", "The owner edits code with Neovim every day.")
    assert service.consolidate(keep, [dup]) == [dup]
    record = store.structured.get(dup)
    assert record["status"] == "superseded" and record["superseded_by"] == keep
    assert "Neovim" in record["body"]  # text preserved
    assert [r["record_id"] for r in store.retrieve_records("neovim editor code")] == [keep]
    db = sqlite3.connect(store.database_path)
    indexed = {row[0] for row in db.execute("SELECT record_id FROM structured_fts")}
    db.close()
    assert indexed == {keep}  # the merged copy is gone from the index itself, not just hidden
    assert service.run()["duplicates"] == []
    assert store.audit.list(target_id=dup)[0]["action"] == "record_consolidated"
    # deleting the kept record removes the merged copy too: forgetting stays complete
    store.structured.delete(keep)
    assert store.structured.get(dup) is None


def test_consolidate_validates_its_inputs(tmp_path):
    store, service = make(tmp_path)
    a = add(store, "fact", "A", "Alpha beta gamma delta")
    b = add(store, "preference", "B", "Alpha beta gamma delta")
    archived = add(store, "fact", "C", "Epsilon zeta eta theta")
    store.structured.archive(archived)
    with pytest.raises(ValueError):
        service.consolidate(a, [])
    with pytest.raises(ValueError):
        service.consolidate(a, [a])
    with pytest.raises(ValueError, match="same type"):
        service.consolidate(a, [b])
    with pytest.raises(RecordStateError):
        service.consolidate(a, [archived])
    assert store.structured.get(a)["status"] == "active"


# ------------------------------------------------------------------ conflicts


def test_conflicting_records_are_reported_never_resolved(tmp_path):
    store, service = make(tmp_path)
    neovim = add(store, "preference", "Favorite editor", "The owner's favorite editor is Neovim")
    helix = add(store, "preference", "Favorite editor", "The owner's favorite editor is now Helix")
    add(store, "preference", "Terminal", "The owner uses the Kitty terminal")
    report = service.run()
    assert len(report["conflicts"]) == 1
    conflict = report["conflicts"][0]
    assert set(conflict["record_ids"]) == {neovim, helix} and conflict["reason"] == "same_subject"
    assert store.structured.get(neovim)["status"] == store.structured.get(helix)["status"] == "active"
    assert report["duplicates"] == []


def test_corrected_records_are_not_reported_as_conflicts(tmp_path):
    store, service = make(tmp_path)
    old = add(store, "decision", "Billing database", "Use PostgreSQL for the invoices")
    new, _ = store.structured.supersede(old, body="Use MySQL for the invoices")
    report = service.run()
    assert report["conflicts"] == [] and store.structured.get(old)["status"] == "superseded"
    assert store.structured.get(new)["status"] == "active"


# ---------------------------------------------------------------------- stale


def test_stale_goals_and_projects_are_flagged_but_decisions_and_preferences_are_not(tmp_path):
    store, service = make(tmp_path)
    old_goal = add(store, "goal", "Ship beta", "Ship the beta release in March")
    old_decision = add(store, "decision", "Billing database", "Use PostgreSQL for invoices")
    old_pref = add(store, "preference", "Answer style", "Short answers with code first")
    recent_goal = add(store, "goal", "Write docs", "Write the user documentation")
    used_project = add(store, "project", "Mia memory", "Persistent memory project work")
    for record_id in (old_goal, old_decision, old_pref, used_project):
        backdate(store, record_id, updated_at=iso(days=-400))
    backdate(store, used_project, last_used_at=iso(days=-2))
    report = service.run(stale_days=180)
    flagged = {item["record_id"]: item for item in report["possibly_stale"]}
    assert set(flagged) == {old_goal}
    assert flagged[old_goal]["days_since_update"] >= 399
    assert store.structured.get(old_goal)["status"] == "active"  # a suggestion only
    assert recent_goal not in flagged and old_decision not in flagged
    with pytest.raises(ValueError):
        service.run(stale_days=0)


# ---------------------------------------------------- confidence reinforcement


def test_repeating_an_automatic_suggestion_raises_confidence_but_not_trust(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    capture = CaptureService(store)
    conv = store.ensure_conversation("conv-maint-1")

    def say(text):
        message_id = store.append_message(conv, "user", text)
        return capture.process_message(conv, message_id, text)

    first = say("I always review pull requests before lunch.")
    record_id = first["pending"][0]["record_id"]
    assert store.structured.get(record_id)["confidence"] == 0.7
    say("I always review the pull requests before lunch.")
    record = store.structured.get(record_id)
    assert record["confidence"] == pytest.approx(0.8)
    assert record["provenance"] == "observed" and record["status"] == "pending"
    for _ in range(5):
        say(f"I always review pull requests before lunch, {_}.")
    assert store.structured.get(record_id)["confidence"] <= 0.95


def test_owner_restating_a_suggestion_explicitly_confirms_it(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    capture = CaptureService(store)
    conv = store.ensure_conversation("conv-maint-2")
    message_id = store.append_message(conv, "user", "I always review pull requests before lunch.")
    pending = capture.process_message(conv, message_id, "I always review pull requests before lunch.")
    record_id = pending["pending"][0]["record_id"]
    text = "Remember that I always review pull requests before lunch."
    message_id = store.append_message(conv, "user", text)
    result = capture.process_message(conv, message_id, text)
    assert result["skipped"][0]["confirmed"] is True and result["saved"] == []
    record = store.structured.get(record_id)
    assert record["provenance"] == "owner_provided"
    assert record["origin"]["provenance_before_confirmation"] == "observed"


# ------------------------------------------------------- history is preserved


def test_maintenance_never_rewrites_a_decision_or_its_history(tmp_path):
    store, service = make(tmp_path)
    decision = add(store, "decision", "Billing database", "Use PostgreSQL for invoices",
                   details={"rationale": "transactions"})
    new, _ = store.structured.supersede(decision, body="Use MySQL for invoices")
    backdate(store, decision, updated_at=iso(days=-900))
    backdate(store, new, updated_at=iso(days=-900))
    before = json.dumps([store.structured.get(i) for i in (decision, new)], sort_keys=True, default=str)
    service.run(apply=True, stale_days=30)
    after = json.dumps([store.structured.get(i) for i in (decision, new)], sort_keys=True, default=str)
    assert before == after


# ------------------------------------------------------------------ API level


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()), memory=memory))
    return client, memory


def test_maintenance_and_consolidate_endpoints(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    keep = add(memory, "fact", "Editor", "The owner edits code with Neovim every day")
    dup = add(memory, "fact", "Editor copy", "The owner edits code with Neovim every day.")
    report = client.post("/api/memory/maintenance", json={}).json()
    assert report["applied"] is False and report["duplicates"][0]["suggested_keep"] == keep

    assert client.post("/api/memory/maintenance", json={"stale_days": 0}).status_code == 422
    assert client.post("/api/memory/maintenance", json={"bogus": 1}).status_code == 422
    assert client.post("/api/memory/records/consolidate",
                       json={"keep_id": keep, "merge_ids": []}).status_code == 422
    assert client.post("/api/memory/records/consolidate",
                       json={"keep_id": keep, "merge_ids": [keep]}).status_code == 400
    assert client.post("/api/memory/records/consolidate",
                       json={"keep_id": keep, "merge_ids": ["missing"]}).status_code == 404
    ok = client.post("/api/memory/records/consolidate", json={"keep_id": keep, "merge_ids": [dup]})
    assert ok.status_code == 200 and ok.json()["merged"] == [dup]
    assert client.post("/api/memory/maintenance", json={"apply": True}).json()["duplicates"] == []


def test_app_start_tidies_expired_temporary_memories(tmp_path, monkeypatch):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    temp = add(memory, "fact", "Temp", "A short lived memory", temporary=True, expires_at=iso(days=1))
    backdate(memory, temp, expires_at=iso(hours=-1))
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    create_app(store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
               gateway=ModelGateway(ProviderRegistry()), memory=memory)
    assert memory.structured.get(temp)["status"] == "expired"


def test_repeated_mentions_never_change_an_owner_provided_memorys_confidence(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    capture = CaptureService(store)
    conv = store.ensure_conversation("conv-maint-3")
    record_id = add(store, "preference", "Review habit", "I always review pull requests before lunch.",
                    confidence=0.6)
    assert store.structured.get(record_id)["provenance"] == "owner_provided"
    for index in range(3):
        text = f"I always review pull requests before lunch, really {index}."
        message_id = store.append_message(conv, "user", text)
        capture.process_message(conv, message_id, text)
    record = store.structured.get(record_id)
    assert record["confidence"] == 0.6 and record["provenance"] == "owner_provided"
    assert len(store.structured.list(limit=20)) == 1


def test_duplicates_are_recognised_across_record_types(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    capture = CaptureService(store)
    conv = store.ensure_conversation("conv-maint-4")
    existing = add(store, "fact", "Review habit", "I always review pull requests before lunch.")
    text = "Remember that I always review pull requests before lunch."
    message_id = store.append_message(conv, "user", text)
    result = capture.process_message(conv, message_id, text)
    assert result["saved"] == [] and result["skipped"][0]["record_id"] == existing
    assert len(store.structured.list(limit=20)) == 1
