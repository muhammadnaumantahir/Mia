import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.relationships import RelationshipNotFoundError
from mia.memory.store import MemoryStore
from mia.memory.structured import RecordNotFoundError, RecordStateError
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def make_store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def sql(store, query, params=()):
    db = sqlite3.connect(store.database_path)
    try:
        return db.execute(query, params).fetchall()
    finally:
        db.close()


def iso(**delta):
    return (datetime.now(UTC) + timedelta(**delta)).isoformat()


def retrievable(store, text):
    return [r["record_id"] for r in store.retrieve_records(text)]


def add(store, record_type="fact", title="Preferred editor", body="Uses Neovim for coding", **kw):
    record_id, _ = store.structured.add(record_type=record_type, title=title, body=body, **kw)
    return record_id


# ------------------------------------------------------------------ record types


@pytest.mark.parametrize("record_type", ["fact", "preference", "lesson", "decision", "entity"])
def test_semantic_and_other_record_types_are_accepted(tmp_path, record_type):
    store = make_store(tmp_path)
    record_id = add(store, record_type)
    assert store.structured.get(record_id)["record_type"] == record_type


def test_unknown_record_type_and_secret_content_are_rejected(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        add(store, "mood")
    with pytest.raises(ValueError, match="credential|looks like"):
        add(store, title="Server login", body="the password is hunter2-hunter2")
    with pytest.raises(ValueError):
        add(store, details={"note": "token ghp_" + "b" * 36})
    assert store.structured.counts() == {}


# --------------------------------------------------------------- pending / approve


def test_pending_suggestion_is_never_retrieved_until_approved(tmp_path):
    store = make_store(tmp_path)
    record_id = add(
        store, status="pending", provenance="observed",
        origin={"capture": "auto", "conversation_id": "conv-aaaaaaaa"},
    )
    assert retrievable(store, "neovim coding editor") == []
    assert sql(store, "SELECT COUNT(*) FROM structured_fts")[0][0] == 0

    competing = store.structured.approve(record_id)
    assert competing == []
    record = store.structured.get(record_id)
    assert record["status"] == "active" and record["provenance"] == "owner_provided"
    assert record["origin"]["provenance_before_approval"] == "observed"
    assert record["origin"]["conversation_id"] == "conv-aaaaaaaa"
    assert retrievable(store, "neovim coding editor") == [record_id]

    with pytest.raises(RecordStateError):
        store.structured.approve(record_id)  # no longer pending


def test_pending_can_be_rejected_but_not_activated_by_status_change(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store, status="pending")
    with pytest.raises(RecordStateError):
        store.structured.set_status(record_id, "active")
    store.structured.set_status(record_id, "rejected")
    assert store.structured.get(record_id)["status"] == "rejected"
    assert retrievable(store, "neovim coding editor") == []


def test_approving_surfaces_competing_active_records(tmp_path):
    store = make_store(tmp_path)
    first = add(store)
    pending = add(store, status="pending", body="Uses Helix for coding now")
    assert store.structured.approve(pending) == [first]


# ----------------------------------------------------------- archive / outdated


def test_archive_removes_from_retrieval_and_restore_returns_it(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store)
    store.structured.archive(record_id)
    record = store.structured.get(record_id)
    assert record["status"] == "archived" and record["archived_at"]
    assert retrievable(store, "neovim coding editor") == []
    assert sql(store, "SELECT COUNT(*) FROM structured_fts")[0][0] == 0

    store.structured.restore(record_id)
    assert store.structured.get(record_id)["status"] == "active"
    assert store.structured.get(record_id)["archived_at"] is None
    assert retrievable(store, "neovim coding editor") == [record_id]


def test_outdated_records_are_kept_but_not_retrieved_and_can_be_restored_or_corrected(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store)
    store.structured.mark_outdated(record_id)
    assert store.structured.get(record_id)["status"] == "outdated"
    assert retrievable(store, "neovim coding editor") == []

    new_id, _ = store.structured.supersede(record_id, body="Uses Zed for coding")
    assert store.structured.get(record_id)["status"] == "superseded"
    assert retrievable(store, "zed coding") == [new_id]

    other = add(store, title="Another", body="Another outdated fact")
    store.structured.mark_outdated(other)
    store.structured.restore(other)
    assert store.structured.get(other)["status"] == "active"


def test_invalid_lifecycle_transitions_are_refused(tmp_path):
    store = make_store(tmp_path)
    pending = add(store, status="pending")
    with pytest.raises(RecordStateError):
        store.structured.archive(pending)
    with pytest.raises(RecordStateError):
        store.structured.restore(pending)
    done = add(store, title="x", body="y z")
    store.structured.archive(done)
    with pytest.raises(RecordStateError):
        store.structured.mark_outdated(done)
    with pytest.raises(RecordNotFoundError):
        store.structured.archive("missing")


# ------------------------------------------------------------------------- edit


def test_edit_snapshots_previous_version_reindexes_and_marks_owner_confirmed(tmp_path):
    store = make_store(tmp_path)
    record_id = add(
        store, provenance="inferred", evidence=[{"type": "message", "ref": "m1"}], confidence=0.6
    )
    conflicts = store.structured.edit(record_id, body="Uses Helix for coding", importance=0.9)
    assert conflicts == []
    record = store.structured.get(record_id)
    assert record["body"] == "Uses Helix for coding" and record["importance"] == 0.9
    assert record["provenance"] == "owner_provided"
    assert retrievable(store, "helix coding") == [record_id]
    assert retrievable(store, "neovim") == []

    history = store.structured.edit_history(record_id)
    assert len(history) == 1 and history[0]["change_type"] == "edit"
    assert history[0]["snapshot"]["body"] == "Uses Neovim for coding"
    assert history[0]["snapshot"]["provenance"] == "inferred"


def test_decisions_cannot_be_edited_in_place_only_corrected(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store, "decision", "Billing database", "Use PostgreSQL")
    with pytest.raises(RecordStateError, match="corrected"):
        store.structured.edit(record_id, body="Use MySQL")
    with pytest.raises(RecordStateError):
        store.structured.edit(record_id, title="Renamed")
    store.structured.edit(record_id, importance=0.95)  # metadata only is fine
    assert store.structured.get(record_id)["body"] == "Use PostgreSQL"
    new_id, _ = store.structured.supersede(record_id, body="Use MySQL")
    assert store.structured.get(record_id)["body"] == "Use PostgreSQL"  # original preserved
    assert store.structured.get(new_id)["body"] == "Use MySQL"


def test_edit_validation_and_secret_rejection(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store)
    with pytest.raises(ValueError):
        store.structured.edit(record_id)
    with pytest.raises(ValueError):
        store.structured.edit(record_id, importance=2)
    with pytest.raises(ValueError):
        store.structured.edit(record_id, body="   ")
    with pytest.raises(ValueError, match="looks like"):
        store.structured.edit(record_id, body="api_key = sk-abcdefghijklmnopqrstuvwxyz")
    assert store.structured.get(record_id)["body"] == "Uses Neovim for coding"
    store.structured.mark_outdated(record_id)
    store.structured.supersede(record_id, body="Uses Zed")
    with pytest.raises(RecordStateError):
        store.structured.edit(record_id, body="Uses Emacs")


def test_confirm_promotes_inferred_to_owner_provided(tmp_path):
    store = make_store(tmp_path)
    record_id = add(
        store, provenance="inferred", evidence=[{"type": "message", "ref": "m1"}]
    )
    store.structured.confirm(record_id)
    record = store.structured.get(record_id)
    assert record["provenance"] == "owner_provided"
    assert record["origin"]["provenance_before_confirmation"] == "inferred"
    assert store.structured.edit_history(record_id)[0]["change_type"] == "confirm"


# ------------------------------------------------------- temporary and expiry


def test_temporary_memory_expires_and_stops_being_retrieved(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store, temporary=True, expires_at=iso(days=1))
    assert retrievable(store, "neovim coding editor") == [record_id]
    assert store.structured.expire_due() == []

    assert store.structured.expire_due(now=iso(days=2)) == [record_id]
    assert store.structured.get(record_id)["status"] == "expired"
    assert retrievable(store, "neovim coding editor") == []
    assert sql(store, "SELECT COUNT(*) FROM structured_fts")[0][0] == 0
    with pytest.raises(RecordStateError):
        store.structured.edit(record_id, body="revived")
    assert store.structured.expire_due(now=iso(days=3)) == []  # idempotent


def test_past_expiry_is_not_retrieved_even_before_the_sweep_runs(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store)
    store.structured.set_temporary(record_id, temporary=True, expires_at=iso(days=1))
    db = sqlite3.connect(store.database_path)
    db.execute(
        "UPDATE structured_memory SET expires_at = ? WHERE record_id = ?",
        (iso(hours=-1), record_id),
    )
    db.commit()
    db.close()
    assert retrievable(store, "neovim coding editor") == []


def test_temporary_requires_expiry_and_can_be_made_permanent(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        add(store, temporary=True)
    with pytest.raises(ValueError):
        add(store, temporary=True, expires_at="not a date")
    record_id = add(store)
    with pytest.raises(ValueError):
        store.structured.set_temporary(record_id, temporary=True)
    store.structured.set_temporary(record_id, temporary=True, expires_at=iso(days=3))
    assert store.structured.get(record_id)["temporary"] is True
    store.structured.set_temporary(record_id, temporary=False)
    record = store.structured.get(record_id)
    assert record["temporary"] is False and record["expires_at"] is None


def test_unreviewed_suggestions_expire_after_the_retention_window(tmp_path):
    store = make_store(tmp_path)
    old = add(store, status="pending", title="Old", body="Old suggestion text")
    fresh = add(store, status="pending", title="New", body="New suggestion text")
    db = sqlite3.connect(store.database_path)
    db.execute(
        "UPDATE structured_memory SET created_at = ? WHERE record_id = ?",
        (iso(days=-40), old),
    )
    db.commit()
    db.close()
    assert store.structured.expire_pending(older_than_days=30) == [old]
    assert store.structured.get(old)["status"] == "expired"
    assert store.structured.get(fresh)["status"] == "pending"
    with pytest.raises(ValueError):
        store.structured.expire_pending(older_than_days=0)


# --------------------------------------------------------------------- deletion


def test_delete_removes_whole_correction_chain_history_links_and_usage(tmp_path):
    store = make_store(tmp_path)
    first = add(store, "decision", "Billing database", "Use PostgreSQL")
    second, _ = store.structured.supersede(first, body="Use MySQL")
    third, _ = store.structured.supersede(second, body="Use SQLite")
    store.structured.mark_outdated(third)  # creates a history snapshot containing the text
    store.structured.restore(third)
    assert sql(store, "SELECT COUNT(*) FROM structured_memory_history WHERE record_id = ?", (third,))[0][0] == 2
    other = add(store, "project", "Billing service", "The billing service project")
    store.structured.edit(other, importance=0.7)  # no content change on a project
    store.structured.edit(other, body="Billing service rewrite project")
    store.relationships.add(third, other, "part_of")
    store.usage.record(
        conversation_id="conv-aaaaaaaa", reply_id="r1",
        references=[{"ref_type": "record", "ref_id": third, "title": "Billing database"}],
    )
    assert store.structured.delete(second) is True  # delete from the middle of the chain

    for record_id in (first, second, third):
        assert store.structured.get(record_id) is None
    assert store.structured.get(other) is not None  # unrelated record untouched
    assert sql(store, "SELECT COUNT(*) FROM structured_memory")[0][0] == 1
    assert sql(store, "SELECT COUNT(*) FROM memory_relationships")[0][0] == 0
    assert sql(store, "SELECT COUNT(*) FROM memory_usage")[0][0] == 0
    leftovers = sql(store, "SELECT snapshot_json FROM structured_memory_history")
    assert all("SQLite" not in row[0] for row in leftovers)
    assert sql(store, "SELECT COUNT(*) FROM structured_memory_history WHERE record_id = ?", (third,))[0][0] == 0
    assert retrievable(store, "postgresql mysql sqlite") == []
    assert not {first, second, third} & set(retrievable(store, "billing database postgresql"))
    audit = json.dumps(store.audit.list(limit=200))
    assert "PostgreSQL" not in audit and "MySQL" not in audit and "SQLite" not in audit


def test_delete_without_chain_removes_only_that_record_and_unlinks_neighbours(tmp_path):
    store = make_store(tmp_path)
    first = add(store, "decision", "Billing database", "Use PostgreSQL")
    second, _ = store.structured.supersede(first, body="Use MySQL")
    assert store.structured.delete(second, chain=False) is True
    assert store.structured.get(second) is None
    original = store.structured.get(first)
    assert original is not None and original["superseded_by"] is None


def test_delete_unknown_record_returns_false(tmp_path):
    assert make_store(tmp_path).structured.delete("nope") is False


# ---------------------------------------------------------------------- audit


def test_lifecycle_changes_are_audited_without_memory_text(tmp_path):
    store = make_store(tmp_path)
    record_id = add(store, body="A very private detail about the owner")
    store.structured.edit(record_id, body="Another very private detail")
    store.structured.archive(record_id)
    store.structured.restore(record_id)
    store.structured.delete(record_id)
    entries = store.audit.list(target_type="memory_record", target_id=record_id)
    assert [e["action"] for e in entries][::-1] == [
        "record_created", "record_edited", "record_archived", "record_restored", "record_deleted",
    ]
    assert all(e["actor"] == "owner" for e in entries)
    assert "private detail" not in json.dumps(entries)


# ---------------------------------------------------------------- relationships


def test_relationships_are_idempotent_validated_and_privacy_aware(tmp_path):
    store = make_store(tmp_path)
    person = add(store, "entity", "Sam", "Sam is a collaborator")
    project = add(store, "project", "Mia memory", "The persistent memory project")
    rel_id, created = store.relationships.add(person, project, "works_on", note="since spring")
    again, created_again = store.relationships.add(person, project, "works_on")
    assert created is True and created_again is False and again == rel_id

    with pytest.raises(ValueError):
        store.relationships.add(person, project, "loves")
    with pytest.raises(ValueError):
        store.relationships.add(person, person, "related_to")
    with pytest.raises(ValueError):
        store.relationships.add(person, project, "related_to", visibility="secret")
    with pytest.raises(ValueError, match="looks like"):
        store.relationships.add(person, project, "related_to", note="password: abcdefgh123")
    with pytest.raises(KeyError):
        store.relationships.add(person, "missing", "related_to")

    assert [n["relation"] for n in store.relationships.neighbours([project])] == ["works_on"]
    private_id, _ = store.relationships.add(project, person, "involves", visibility="private")
    assert len(store.relationships.for_record(project)) == 2
    assert [n["relation"] for n in store.relationships.neighbours([project])] == ["works_on"]
    store.relationships.set_visibility(private_id, "normal")
    assert len(store.relationships.neighbours([project])) == 2

    store.structured.archive(person)  # an archived endpoint must not leak through a link
    assert store.relationships.neighbours([project]) == []

    assert store.relationships.remove(rel_id) is True
    assert store.relationships.remove(rel_id) is False
    with pytest.raises(RelationshipNotFoundError):
        store.relationships.set_visibility("missing", "normal")


# --------------------------------------------------------------------- settings


def test_settings_defaults_persist_across_restart_and_validate(tmp_path):
    store = make_store(tmp_path)
    defaults = store.settings.get_all()
    assert defaults["capture_mode"] == "approval" and defaults["memory_enabled"] is True
    assert defaults["retrieval_enabled"] is True and defaults["conversation_retention_days"] is None

    store.settings.update({"capture_mode": "auto", "pending_retention_days": 7})
    reopened = MemoryStore(store.database_path)
    assert reopened.settings.get("capture_mode") == "auto"
    assert reopened.settings.get("pending_retention_days") == 7

    for bad in (
        {"capture_mode": "sometimes"},
        {"memory_enabled": "yes"},
        {"pending_retention_days": 0},
        {"pending_retention_days": True},
        {"auto_save_min_confidence": 0.1},
        {"conversation_retention_days": -3},
        {"nonsense": 1},
    ):
        with pytest.raises(ValueError):
            store.settings.update(bad)
    with pytest.raises(ValueError):
        store.settings.update({})
    assert store.settings.get("capture_mode") == "auto"


def test_settings_changes_are_audited_and_noops_are_not(tmp_path):
    store = make_store(tmp_path)
    store.settings.update({"capture_mode": "off"})
    store.settings.update({"capture_mode": "off"})
    entries = store.audit.list(target_type="memory_setting")
    assert len(entries) == 1
    assert entries[0]["detail"] == {"from": "approval", "to": "off"}


def test_corrupt_stored_setting_falls_back_to_the_safe_default(tmp_path):
    store = make_store(tmp_path)
    db = sqlite3.connect(store.database_path)
    db.execute(
        "INSERT INTO memory_settings (key, value_json, updated_at) VALUES "
        "('capture_mode', '\"bogus\"', 'x')"
    )
    db.commit()
    db.close()
    assert store.settings.get("capture_mode") == "approval"


# ------------------------------------------------- temporary conversations, usage


def test_temporary_conversations_are_not_retrieved_into_other_conversations(tmp_path):
    store = make_store(tmp_path)
    normal = store.ensure_conversation("normal-conversation")
    scratch = store.ensure_conversation("scratch-conversation")
    store.append_message(normal, "user", "The zebra migration plan uses blue servers")
    store.append_message(scratch, "user", "The zebra migration secret codename is Falcon")
    assert {m["conversation_id"] for m in store.retrieve_messages("zebra migration")} == {
        normal, scratch,
    }
    assert store.set_conversation_temporary(scratch, True) is True
    assert store.is_conversation_temporary(scratch) is True
    assert {m["conversation_id"] for m in store.retrieve_messages("zebra migration")} == {normal}
    store.set_conversation_temporary(scratch, False)
    assert len(store.retrieve_messages("zebra migration")) == 2
    assert store.set_conversation_temporary("missing-conversation", True) is False


def test_usage_log_is_idempotent_and_cleaned_up_with_its_subject(tmp_path):
    store = make_store(tmp_path)
    conversation = store.ensure_conversation("usage-conversation")
    item_id = store.add_item(kind="fact", title="Fact", content="Some fact text", trust_level="user")
    record_id = add(store)
    refs = [
        {"ref_type": "knowledge", "ref_id": item_id, "title": "Fact"},
        {"ref_type": "record", "ref_id": record_id, "title": "Preferred editor"},
    ]
    assert store.usage.record(conversation_id=conversation, reply_id="r1", references=refs) == 2
    assert store.usage.record(conversation_id=conversation, reply_id="r1", references=refs) == 2
    assert len(store.usage.for_reply("r1")) == 2  # a retry did not duplicate rows
    assert [u["reply_id"] for u in store.usage.for_memory("record", record_id)] == ["r1"]

    store.delete_item(item_id)
    assert [u["ref_type"] for u in store.usage.for_reply("r1")] == ["record"]
    store.delete_conversation(conversation)
    assert store.usage.for_reply("r1") == []


# ----------------------------------------------------------------- migration


def test_schema_two_database_is_upgraded_with_defaults_and_data_intact(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = MemoryStore(path)
    record_id = add(store, "decision", "Billing database", "Use PostgreSQL")
    db = sqlite3.connect(path)
    for column in (
        "importance", "temporary", "expires_at", "archived_at",
        "origin_json", "last_used_at", "use_count",
    ):
        db.execute(f"ALTER TABLE structured_memory DROP COLUMN {column}")
    db.execute("ALTER TABLE conversations DROP COLUMN temporary")
    for table in (
        "structured_memory_history", "memory_relationships", "memory_settings", "memory_usage",
    ):
        db.execute(f"DROP TABLE {table}")
    db.execute("DELETE FROM schema_migrations WHERE version = 3")
    db.commit()
    db.close()

    upgraded = MemoryStore(path)
    record = upgraded.structured.get(record_id)
    assert record["body"] == "Use PostgreSQL" and record["importance"] == 0.5
    assert record["temporary"] is False and record["origin"] == {}
    assert retrievable(upgraded, "billing database postgresql") == [record_id]
    assert upgraded.settings.get("capture_mode") == "approval"


# ------------------------------------------------------------------- API level


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            gateway=ModelGateway(ProviderRegistry()),
            memory=memory,
        )
    )
    return client, memory


def test_api_lifecycle_endpoints(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    created = client.post(
        "/api/memory/records",
        json={"record_type": "preference", "title": "Answer style", "body": "Short answers",
              "importance": 0.8},
    )
    assert created.status_code == 201
    record_id = created.json()["record_id"]

    edited = client.post(f"/api/memory/records/{record_id}/edit", json={"body": "Very short answers"})
    assert edited.status_code == 200
    assert client.post(f"/api/memory/records/{record_id}/edit", json={}).status_code == 400
    assert client.post(f"/api/memory/records/{record_id}/edit", json={"bogus": 1}).status_code == 422
    assert client.post(
        f"/api/memory/records/{record_id}/edit", json={"body": "password: abcdef12345"}
    ).status_code == 400

    detail = client.get(f"/api/memory/records/{record_id}").json()
    assert detail["record"]["importance"] == 0.8
    assert detail["edit_history"][0]["snapshot"]["body"] == "Short answers"

    assert client.post(f"/api/memory/records/{record_id}/archive").status_code == 200
    listing = client.get("/api/memory/records", params={"status": "archived"}).json()
    assert [r["record_id"] for r in listing["items"]] == [record_id]
    assert listing["counts"] == {"archived": 1}
    assert client.post(f"/api/memory/records/{record_id}/archive").status_code == 409
    assert client.post(f"/api/memory/records/{record_id}/restore").status_code == 200
    assert client.post(f"/api/memory/records/{record_id}/outdated").status_code == 200
    assert client.post(f"/api/memory/records/{record_id}/restore").status_code == 200

    soon = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    ok = client.post(
        f"/api/memory/records/{record_id}/temporary", json={"temporary": True, "expires_at": soon}
    )
    assert ok.status_code == 200
    assert client.post(
        f"/api/memory/records/{record_id}/temporary", json={"temporary": True}
    ).status_code == 400

    assert client.post("/api/memory/records/missing/archive").status_code == 404
    assert client.post("/api/memory/records/missing/approve").status_code == 404
    assert client.post(f"/api/memory/records/{record_id}/approve").status_code == 409

    pending = memory.structured.add(
        record_type="fact", title="Pending thing", body="A pending suggestion", status="pending",
        provenance="observed", actor="capture",
    )[0]
    assert client.post(f"/api/memory/records/{pending}/approve").status_code == 200
    assert memory.structured.get(pending)["status"] == "active"

    assert client.post(f"/api/memory/records/{pending}/confirm").status_code == 200
    assert client.delete(f"/api/memory/records/{record_id}").json() == {
        "deleted": True, "purged_messages": 0,
    }
    assert client.get(f"/api/memory/records/{record_id}").status_code == 404


def test_api_record_search_treats_wildcards_literally(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    add(memory, title="Plain note", body="Nothing special here")
    add(memory, title="Discount", body="Gets a 50% discount")
    everything = client.get("/api/memory/records", params={"q": "%"}).json()["items"]
    assert [r["title"] for r in everything] == ["Discount"]
    assert client.get("/api/memory/records", params={"q": "_"}).json()["items"] == []
    assert len(client.get("/api/memory/records", params={"q": "NOTHING"}).json()["items"]) == 1


def test_api_settings_relationships_temporary_conversation_and_usage(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    assert client.get("/api/memory/settings").json()["capture_mode"] == "approval"
    updated = client.put(
        "/api/memory/settings", json={"capture_mode": "off", "conversation_retention_days": 90}
    )
    assert updated.status_code == 200 and updated.json()["capture_mode"] == "off"
    assert client.put("/api/memory/settings", json={"conversation_retention_days": None}).json()[
        "conversation_retention_days"
    ] is None
    assert client.put("/api/memory/settings", json={"capture_mode": "x"}).status_code == 400
    assert client.put("/api/memory/settings", json={"other": 1}).status_code == 422
    assert client.put("/api/memory/settings", json={}).status_code == 400

    a = add(memory, "entity", "Sam", "A collaborator named Sam")
    b = add(memory, "project", "Mia", "The memory project")
    made = client.post(
        "/api/memory/relationships", json={"from_id": a, "to_id": b, "relation": "works_on"}
    )
    assert made.status_code == 201 and made.json()["created"] is True
    again = client.post(
        "/api/memory/relationships", json={"from_id": a, "to_id": b, "relation": "works_on"}
    )
    assert again.json()["created"] is False
    rel_id = made.json()["relationship_id"]
    assert client.post(
        "/api/memory/relationships", json={"from_id": a, "to_id": "nope", "relation": "part_of"}
    ).status_code == 404
    assert client.post(
        "/api/memory/relationships", json={"from_id": a, "to_id": b, "relation": "hates"}
    ).status_code == 400
    assert len(client.get(f"/api/memory/records/{a}/relationships").json()["relationships"]) == 1
    assert client.post(
        f"/api/memory/relationships/{rel_id}/visibility", json={"visibility": "private"}
    ).status_code == 200
    assert client.post(
        "/api/memory/relationships/missing/visibility", json={"visibility": "private"}
    ).status_code == 404
    assert client.delete(f"/api/memory/relationships/{rel_id}").json() == {"deleted": True}

    conversation = memory.ensure_conversation("api-conversation")
    assert client.post(
        f"/api/conversations/{conversation}/temporary", json={"temporary": True}
    ).status_code == 200
    assert memory.is_conversation_temporary(conversation) is True
    assert client.post(
        "/api/conversations/missing-conversation/temporary", json={"temporary": True}
    ).status_code == 404

    memory.usage.record(
        conversation_id=conversation, reply_id="reply-1",
        references=[{"ref_type": "record", "ref_id": a, "title": "Sam"}],
    )
    used = client.get("/api/memory/replies/reply-1/usage").json()["used"]
    assert [u["ref_id"] for u in used] == [a]
    assert client.get(f"/api/memory/records/{a}").json()["used_in_replies"][0]["reply_id"] == "reply-1"
