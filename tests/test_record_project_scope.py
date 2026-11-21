"""Record-level project scoping for structured memory (MIA-BRAIN-06).

Design (opt-in, default unchanged):
- A structured record has an optional ``project`` (NULL = unscoped, the default).
- A conversation with no project keeps the original behaviour: any record is recallable.
- A conversation in a project recalls that project's records and unscoped records, and
  never another project's records (lexical, intent, semantic and linked paths).
- Capture in a project conversation tags new records with that project and never
  confirms, supersedes or updates another project's records.
- Temporary conversations are still never captured from.
"""

import sqlite3

import pytest

from mia.memory.capture import CaptureService
from mia.memory.context import assemble_memory_context, record_in_scope
from mia.memory.schema import LATEST_VERSION
from mia.memory.store import MemoryStore
from mia.memory.structured import RecordNotFoundError

from .test_brain_pipeline import client_for


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def convo(store, cid, *, project=None, temporary=False):
    store.ensure_conversation(cid)
    store.append_message(cid, "user", "hello there")
    if project is not None:
        assert store.set_conversation_project(cid, project)
    if temporary:
        store.set_conversation_temporary(cid, True)
    return cid


def add(store, title, body, *, project=None, record_type="decision"):
    record_id, _ = store.structured.add(
        record_type=record_type, title=title, body=body, project=project
    )
    return record_id


def recalled_ids(store, prompt, cid, **kw):
    packet = assemble_memory_context(store, prompt, cid, **kw)
    return {ref.ref_id for ref in packet.references if ref.ref_type == "structured"}


# ------------------------------------------------------------------ schema


def test_migration_adds_nullable_project_column_and_default_is_unscoped(store):
    assert LATEST_VERSION >= 5
    db = sqlite3.connect(store.database_path)
    try:
        columns = {row[1] for row in db.execute("PRAGMA table_info(structured_memory)")}
    finally:
        db.close()
    assert "project" in columns
    rid = add(store, "Pick a database", "We chose PostgreSQL for billing.")
    assert store.structured.get(rid)["project"] is None


def test_upgrade_from_v4_database_keeps_existing_records_unscoped(tmp_path):
    path = tmp_path / "old.sqlite3"
    first = MemoryStore(path)
    rid = add(first, "Old decision", "Use SQLite for the cache layer.")
    db = sqlite3.connect(path)
    try:
        db.execute("ALTER TABLE structured_memory DROP COLUMN project")
        db.execute("DELETE FROM schema_migrations WHERE version = 5")
        db.commit()
    finally:
        db.close()
    reopened = MemoryStore(path)
    assert reopened.structured.get(rid)["project"] is None
    assert reopened.structured.set_project(rid, "Atlas") is True  # column is usable again


# ------------------------------------------------------------------ store API


def test_add_normalises_and_validates_project(store):
    rid = add(store, "Choose queue", "We chose Redis streams.", project="  Atlas   One ")
    assert store.structured.get(rid)["project"] == "Atlas One"
    with pytest.raises(ValueError):
        add(store, "Bad", "Body text here.", project="x" * 65)
    with pytest.raises(ValueError):
        add(store, "Bad two", "Body text here.", project="   ")


def test_set_project_sets_clears_audits_and_reports_no_change(store):
    rid = add(store, "Choose queue", "We chose Redis streams.")
    assert store.structured.set_project(rid, "Atlas") is True
    assert store.structured.get(rid)["project"] == "Atlas"
    assert store.structured.set_project(rid, "Atlas") is False
    assert store.structured.set_project(rid, None) is True
    assert store.structured.get(rid)["project"] is None
    entries = store.audit.list(target_type="memory_record", target_id=rid)
    assert [e["action"] for e in entries].count("record_project_set") == 2
    with pytest.raises(RecordNotFoundError):
        store.structured.set_project("missing", "Atlas")


def test_correction_keeps_the_project_scope(store):
    rid = add(store, "Choose queue", "We chose Redis streams.", project="Atlas")
    new_id, _ = store.structured.supersede(rid, body="We chose NATS JetStream.")
    assert store.structured.get(new_id)["project"] == "Atlas"


# ------------------------------------------------------------------ recall


def test_project_conversation_never_recalls_another_projects_record(store):
    mine = add(store, "Billing database", "Atlas billing database is PostgreSQL.", project="Atlas")
    other = add(store, "Billing database", "Borealis billing database is MySQL.",
                project="Borealis")
    free = add(store, "Billing database rule", "Billing database changes need review.")
    cid = convo(store, "conv-atlas-1", project="atlas")  # case-insensitive match
    ids = recalled_ids(store, "what is the billing database", cid)
    assert mine in ids and free in ids
    assert other not in ids


def test_conversation_without_project_keeps_original_behaviour(store):
    atlas = add(store, "Billing database", "Atlas billing database is PostgreSQL.",
                project="Atlas")
    other = add(store, "Billing database two", "Borealis billing database is MySQL.",
                project="Borealis")
    cid = convo(store, "conv-none-1")
    assert {atlas, other} <= recalled_ids(store, "what is the billing database", cid)


def test_intent_path_respects_project_scope(store):
    mine = add(store, "Alpha plan", "Atlas plan is to ship the importer first.", project="Atlas")
    other = add(store, "Beta plan", "Borealis plan is to ship the exporter first.",
                project="Borealis")
    cid = convo(store, "conv-atlas-2", project="Atlas")
    # no shared distinctive words: only the "where did we leave off" intent can surface them
    ids = recalled_ids(store, "where did we leave off on the project", cid)
    assert other not in ids
    assert mine in ids


def test_linked_records_do_not_cross_project_scope(store):
    mine = add(store, "Billing database", "Atlas billing database is PostgreSQL.", project="Atlas")
    other = add(store, "Exporter choice", "Borealis exporter uses protobuf.", project="Borealis")
    store.relationships.add(mine, other, "part_of")
    cid = convo(store, "conv-atlas-3", project="Atlas")
    ids = recalled_ids(store, "what is the billing database", cid)
    assert mine in ids
    assert other not in ids


def test_semantic_candidates_respect_project_scope(store):
    other = add(store, "Exporter choice", "Borealis exporter uses protobuf.", project="Borealis")
    free = add(store, "Exporter note", "Exporters need a schema.")
    cid = convo(store, "conv-atlas-4", project="Atlas")

    def reranker(prompt, candidates):  # boosts every candidate, as a permissive embedder would
        return {
            str(c.get("record_id") or c.get("item_id") or c.get("message_id")): 0.3
            for c in candidates
        }

    ids = recalled_ids(store, "tell me about serialisation formats", cid, reranker=reranker)
    assert other not in ids
    assert free in ids


def test_record_in_scope_rules():
    assert record_in_scope({"project": "Atlas"}, None)
    assert record_in_scope({"project": None}, "atlas")
    assert record_in_scope({"project": "ATLAS"}, "atlas")
    assert not record_in_scope({"project": "Borealis"}, "atlas")


# ------------------------------------------------------------------ capture


def say(store, service, cid, text):
    message_id = store.append_message(cid, "user", text)
    return service.process_message(cid, message_id, text)


def test_explicit_capture_in_project_conversation_tags_the_record(store):
    service = CaptureService(store)
    cid = convo(store, "conv-atlas-5", project="Atlas")
    result = say(store, service, cid, "Remember that Atlas uses PostgreSQL for billing.")
    assert result["saved"]
    assert store.structured.get(result["saved"][0]["record_id"])["project"] == "Atlas"


def test_capture_without_project_stays_unscoped(store):
    service = CaptureService(store)
    cid = convo(store, "conv-none-2")
    result = say(store, service, cid, "Remember that I prefer short answers.")
    assert result["saved"]
    assert store.structured.get(result["saved"][0]["record_id"])["project"] is None


def test_capture_never_supersedes_another_projects_record(store):
    service = CaptureService(store)
    other = add(store, "Billing database", "Borealis billing database is MySQL.",
                project="Borealis", record_type="fact")
    cid = convo(store, "conv-atlas-6", project="Atlas")
    say(store, service, cid, "Remember that billing database is PostgreSQL.")
    record = store.structured.get(other)
    assert record["status"] == "active"
    assert record["superseded_by"] is None


def test_explicit_update_does_not_rewrite_another_projects_record(store):
    service = CaptureService(store)
    other = add(store, "Billing database", "Borealis billing database is MySQL.",
                project="Borealis", record_type="fact")
    cid = convo(store, "conv-atlas-7", project="Atlas")
    say(store, service, cid, "Update billing database: it is now PostgreSQL.")
    assert store.structured.get(other)["status"] == "active"


def test_temporary_project_conversation_is_still_never_captured(store):
    service = CaptureService(store)
    cid = convo(store, "conv-atlas-8", project="Atlas", temporary=True)
    result = say(store, service, cid, "Remember that Atlas uses PostgreSQL for billing.")
    assert not result["saved"]
    assert store.structured.list(limit=10) == []


# ------------------------------------------------------------------ API


def test_api_creates_scoped_record_and_sets_and_clears_project(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    created = client.post(
        "/api/memory/records",
        json={"record_type": "decision", "title": "Queue", "body": "We chose Redis.",
              "project": "Atlas"},
    )
    assert created.status_code == 201
    rid = created.json()["record_id"]
    assert memory.structured.get(rid)["project"] == "Atlas"

    cleared = client.post(f"/api/memory/records/{rid}/project", json={"project": None})
    assert cleared.status_code == 200
    assert cleared.json() == {"record_id": rid, "project": None, "changed": True}

    again = client.post(f"/api/memory/records/{rid}/project", json={"project": None}).json()
    assert again["changed"] is False

    assert client.post(f"/api/memory/records/{rid}/project", json={"project": "x" * 65}
                       ).status_code in {400, 422}
    assert client.post("/api/memory/records/missing/project", json={"project": "A"}
                       ).status_code == 404
    listed = client.get("/api/memory/records").json()["items"]
    assert listed[0]["project"] is None
