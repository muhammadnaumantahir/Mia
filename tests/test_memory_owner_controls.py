"""MIA-M1-10: owner controls over conversation and memory deletion and export.

Covers what a delete or a retention purge leaves behind (usage rows, derived
memories), that the owner can see it, and that the audit trail holds ids and counts
only. Scripted data only; no model is involved.
"""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

OLD = "2000-01-01T00:00:00+00:00"


def sql(store, query, params=()):
    db = sqlite3.connect(store.database_path)
    try:
        return db.execute(query, params).fetchall()
    finally:
        db.close()


def execute(store, query, params=()):
    db = sqlite3.connect(store.database_path)
    try:
        db.execute(query, params)
        db.commit()
    finally:
        db.close()


def seed(store, name, *, text="Favourite editor is Neovim", explicit=False):
    """A conversation with one message, one memory captured from it and one usage row."""
    conversation = store.ensure_conversation(f"conv-{name}-000001")
    message_id = store.append_message(conversation, "user", text)
    record_id, _ = store.structured.add(
        record_type="fact",
        title=f"Editor {name}",
        body=f"{text} ({name})",
        origin={
            "capture": "explicit" if explicit else "auto",
            "conversation_id": conversation,
            "message_id": message_id,
        },
    )
    store.usage.record(
        conversation_id=conversation,
        reply_id=f"reply-{name}",
        references=[{"ref_type": "record", "ref_id": record_id, "title": "Editor"}],
    )
    return conversation, message_id, record_id


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


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


# ----------------------------------------------------------------- retention purge


def test_conversation_retention_purge_removes_usage_rows_and_is_audited(store):
    conversation, _, _ = seed(store, "old")
    keep, _, _ = seed(store, "new")
    execute(store, "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (OLD, conversation))

    result = store.purge_older_than(30, items=False, dry_run=False)

    assert result["conversations"]["count"] == 1
    assert store.get_conversation(conversation) is None
    assert sql(store, "SELECT 1 FROM memory_usage WHERE conversation_id = ?", (conversation,)) == []
    assert sql(store, "SELECT 1 FROM memory_usage WHERE conversation_id = ?", (keep,)) != []
    audit = store.audit.list(target_type="conversation")
    purge = [e for e in audit if e["action"] == "conversations_retention_purge"]
    assert len(purge) == 1
    assert purge[0]["detail"]["count"] == 1
    assert purge[0]["detail"]["ids"] == [conversation]


def test_dry_run_purge_deletes_and_audits_nothing(store):
    conversation, _, _ = seed(store, "old")
    execute(store, "UPDATE conversations SET updated_at = ?", (OLD,))

    result = store.purge_older_than(30, dry_run=True)

    assert result["conversations"]["count"] == 1
    assert store.get_conversation(conversation) is not None
    assert sql(store, "SELECT 1 FROM memory_usage") != []
    assert not [e for e in store.audit.list() if "retention_purge" in e["action"]]


def test_knowledge_retention_purge_removes_usage_rows_and_is_audited(store):
    item_id = store.add_item(kind="note", title="Zebra", content="Zebras have stripes")
    other_id = store.add_item(kind="note", title="Quokka", content="Quokkas are small")
    for ref in (item_id, other_id):
        store.usage.record(
            conversation_id=None,
            reply_id=f"reply-{ref}",
            references=[{"ref_type": "knowledge", "ref_id": ref, "title": "k"}],
        )
    execute(store, "UPDATE memory_items SET updated_at = ? WHERE item_id = ?", (OLD, item_id))

    store.purge_older_than(30, conversations=False, dry_run=False)

    assert store.get_item(item_id) is None
    assert store.search("Zebras") == []
    remaining = sql(store, "SELECT ref_id FROM memory_usage WHERE ref_type = 'knowledge'")
    assert remaining == [(other_id,)]
    entry = [e for e in store.audit.list() if e["action"] == "items_retention_purge"]
    assert len(entry) == 1 and entry[0]["detail"]["ids"] == [item_id]
    assert "Zebras" not in str(entry[0])


# -------------------------------------------------------------- conversation delete


def test_delete_conversation_is_audited_with_ids_and_counts_only(store):
    conversation, _, _ = seed(store, "a", text="My secret nickname is Zorblax")

    assert store.delete_conversation(conversation) is True

    entries = [e for e in store.audit.list(target_type="conversation")
               if e["action"] == "conversation_deleted"]
    assert len(entries) == 1
    assert entries[0]["target_id"] == conversation
    assert entries[0]["detail"] == {"derived_records": 1, "forget_derived": False}
    assert "Zorblax" not in str(entries[0])
    assert store.delete_conversation(conversation) is False  # nothing more to audit
    assert len([e for e in store.audit.list() if e["action"] == "conversation_deleted"]) == 1


def test_delete_conversation_keeps_derived_memory_by_default_but_reports_it(store):
    conversation, _, record_id = seed(store, "a")

    report = store.delete_conversation_report(conversation)

    assert report["deleted"] is True
    assert report["forgotten_records"] == 0
    assert [r["record_id"] for r in report["derived_records"]] == [record_id]
    assert [r["record_id"] for r in store.retrieve_records("Neovim")] == [record_id]


def test_forget_derived_removes_memories_from_this_conversation_only(store):
    conversation, _, record_id = seed(store, "a")
    other, _, other_record = seed(store, "b")

    report = store.delete_conversation_report(conversation, forget_derived=True)

    assert report["deleted"] is True and report["forgotten_records"] == 1
    assert sql(store, "SELECT 1 FROM structured_memory WHERE record_id = ?", (record_id,)) == []
    assert sql(store, "SELECT 1 FROM memory_usage WHERE ref_id = ?", (record_id,)) == []
    recalled = [r["record_id"] for r in store.retrieve_records("Neovim")]
    assert recalled == [other_record]
    assert store.get_conversation(other) is not None


def test_forget_derived_does_not_sweep_away_an_owner_correction(store):
    conversation, _, record_id = seed(store, "a")
    corrected_id, _ = store.structured.supersede(
        record_id, title="Editor a", body="Favourite editor is Helix",
        provenance="owner_provided",
    )

    store.delete_conversation_report(conversation, forget_derived=True)

    # The captured original is forgotten; the owner's own correction survives,
    # with no dangling pointer to the deleted record.
    assert sql(store, "SELECT 1 FROM structured_memory WHERE record_id = ?", (record_id,)) == []
    row = sql(store, "SELECT supersedes_id FROM structured_memory WHERE record_id = ?",
              (corrected_id,))
    assert row == [(None,)]
    assert [r["record_id"] for r in store.retrieve_records("Helix")] == [corrected_id]


def test_forget_derived_audit_records_ids_not_text(store):
    conversation, _, record_id = seed(store, "a", text="Favourite colour is vermilion")

    store.delete_conversation_report(conversation, forget_derived=True)

    entries = store.audit.list()
    assert any(e["action"] == "record_deleted" and e["target_id"] == record_id for e in entries)
    assert "vermilion" not in str(entries)
    deleted = [e for e in entries if e["action"] == "conversation_deleted"][0]
    assert deleted["detail"]["forget_derived"] is True


def test_forget_derived_can_clean_up_after_the_conversation_is_already_gone(store):
    conversation, _, record_id = seed(store, "a")
    store.delete_conversation(conversation)

    preview = store.derived_records_for_conversation(conversation)
    assert [r["record_id"] for r in preview] == [record_id]
    report = store.delete_conversation_report(conversation, forget_derived=True)

    assert report["deleted"] is False and report["forgotten_records"] == 1
    assert store.retrieve_records("Neovim") == []


# ------------------------------------------------------------------------- export


def test_export_includes_conversation_privacy_and_project_controls(store):
    conversation = store.ensure_conversation("conv-export-000001")
    store.append_message(conversation, "user", "hello there")
    store.set_conversation_project(conversation, "Atlas")
    store.set_conversation_temporary(conversation, True)
    plain = store.ensure_conversation("conv-export-000002")
    store.append_message(plain, "user", "hi")

    exported = {c["conversation_id"]: c for c in store.export_data()["conversations"]}

    assert exported[conversation]["temporary"] is True
    assert exported[conversation]["project"] == "Atlas"
    assert exported[plain]["temporary"] is False
    assert exported[plain]["project"] is None
    assert exported[conversation]["messages"][0]["content"] == "hello there"


def test_export_checksum_covers_the_new_fields(store):
    conversation = store.ensure_conversation("conv-export-000003")
    store.append_message(conversation, "user", "hello")
    before = store.export_data()
    store.set_conversation_project(conversation, "Atlas")
    after = store.export_data()
    assert before["sha256"] != after["sha256"]
    assert after["schema_version"] == 1


# ---------------------------------------------------------------------------- API


def test_api_delete_conversation_reports_derived_memory_and_is_opt_in(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    conversation, _, record_id = seed(memory, "a")

    preview = client.get(f"/api/conversations/{conversation}/derived-memory").json()
    assert preview["count"] == 1 and preview["records"][0]["record_id"] == record_id
    assert memory.get_conversation(conversation) is not None  # preview deletes nothing

    outcome = client.delete(f"/api/conversations/{conversation}").json()
    assert outcome["deleted"] is True and outcome["forgotten_records"] == 0
    assert outcome["derived_records"][0]["record_id"] == record_id
    assert client.get(f"/api/memory/records/{record_id}").status_code == 200

    forgotten = client.delete(
        f"/api/conversations/{conversation}", params={"forget_derived": "true"}
    ).json()
    assert forgotten["deleted"] is False and forgotten["forgotten_records"] == 1
    assert client.get(f"/api/memory/records/{record_id}").status_code == 404
    assert client.get("/api/memory/search", params={"q": "Neovim"}).json()["items"] == []


def test_api_delete_with_forget_derived_in_one_call(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    conversation, _, record_id = seed(memory, "a")

    outcome = client.delete(
        f"/api/conversations/{conversation}", params={"forget_derived": "true"}
    ).json()

    assert outcome["deleted"] is True and outcome["forgotten_records"] == 1
    assert client.get(f"/api/memory/records/{record_id}").status_code == 404
    audit = client.get("/api/memory/audit").json()["entries"]
    assert any(e["action"] == "conversation_deleted" for e in audit)


def test_api_delete_unknown_conversation_changes_nothing(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    conversation, _, record_id = seed(memory, "a")

    outcome = client.delete("/api/conversations/conv-missing-000001").json()

    assert outcome == {"deleted": False, "derived_records": [], "forgotten_records": 0}
    assert memory.get_conversation(conversation) is not None
    assert client.get(f"/api/memory/records/{record_id}").status_code == 200


def test_api_export_round_trips_privacy_flags(tmp_path, monkeypatch):
    client, memory = make_client(tmp_path, monkeypatch)
    conversation = memory.ensure_conversation("conv-export-000009")
    memory.set_conversation_temporary(conversation, True)

    payload = client.get("/api/memory/export").json()

    assert payload["conversations"][0]["temporary"] is True
