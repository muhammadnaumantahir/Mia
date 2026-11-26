"""Project workspace: goals, milestones and the isolated cross-store view (MIA-LIFE-04)."""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mia.memory import projects as ws
from mia.memory.projects import MilestoneNotFoundError, ProjectNotFoundError
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def convo(store, cid, project=None, temporary=False, title=None):
    store.ensure_conversation(cid)
    store.append_message(cid, "user", f"hello from {cid}")
    if project:
        assert store.set_conversation_project(cid, project)
    if temporary:
        store.set_conversation_temporary(cid, True)
    return cid


def seed(store):
    """Two projects plus unassigned and private material, all with distinctive words."""
    store.projects.create("Atlas", goal="Ship the atlas importer")
    store.projects.create("Borealis", goal="Ship the borealis exporter")
    store.personal.add(kind="task", title="atlas task", project="Atlas")
    store.personal.add(kind="task", title="Atlas task two", project="atlas")
    store.personal.add(kind="task", title="borealis task", project="Borealis")
    store.personal.add(kind="task", title="loose task")
    store.structured.add(record_type="decision", title="Atlas db",
                         body="Atlas uses PostgreSQL.", project="Atlas")
    store.structured.add(record_type="decision", title="Borealis db",
                         body="Borealis uses MySQL.", project="Borealis")
    store.structured.add(record_type="decision", title="Loose decision", body="Unscoped one.")
    convo(store, "conv-atlas", "Atlas")
    convo(store, "conv-borealis", "Borealis")
    convo(store, "conv-loose")
    convo(store, "conv-atlas-private", "Atlas", temporary=True)


# ------------------------------------------------------------------ isolation


def test_workspace_contains_only_its_own_projects_material(store):
    seed(store)
    view = ws.workspace(store, "ATLAS")  # case-insensitive
    assert {t["title"] for t in view["tasks"]} == {"atlas task", "Atlas task two"}
    assert [d["title"] for d in view["decisions"]] == ["Atlas db"]
    assert [c["conversation_id"] for c in view["conversations"]] == ["conv-atlas"]
    dump = str(view)
    for foreign in ("borealis", "Borealis", "loose", "Loose", "MySQL"):
        assert foreign not in dump
    assert view["counts"] == {"open_tasks": 2, "decisions": 1, "conversations": 1,
                              "open_milestones": 0}


def test_temporary_conversations_never_appear_or_count(store):
    seed(store)
    view = ws.workspace(store, "Atlas")
    assert "conv-atlas-private" not in str(view)
    listing = {p["name"]: p for p in ws.list_projects(store)}
    assert listing["Atlas"]["conversations"] == 1


def test_unassigned_context_is_not_a_project(store):
    seed(store)
    assert "loose" not in str(ws.workspace(store, "Borealis"))
    assert all(p["name"] != "" for p in ws.list_projects(store))
    # a project with no registered workspace still gets an isolated, unregistered view
    store.personal.add(kind="task", title="ghost task", project="Ghost")
    view = ws.workspace(store, "ghost")
    assert view["registered"] is False and view["goal"] == "" and view["status"] is None
    assert [t["title"] for t in view["tasks"]] == ["ghost task"]


def test_finished_tasks_and_retired_decisions_are_hidden(store):
    seed(store)
    atlas_task = store.personal.list(project="Atlas")[0]["item_id"]
    store.personal.complete(atlas_task)
    old = next(r for r in store.structured.list(record_type="decision") if r["title"] == "Atlas db")
    store.structured.supersede(old["record_id"], body="Atlas uses SQLite instead.")
    view = ws.workspace(store, "Atlas")
    assert view["counts"]["open_tasks"] == 1
    assert [d["body"] for d in view["decisions"]] == ["Atlas uses SQLite instead."]
    assert "PostgreSQL" not in str(view)


def test_discovered_projects_are_listed_with_counts(store):
    seed(store)
    store.personal.add(kind="task", title="ghost task", project="Ghost")
    by_name = {p["name"]: p for p in ws.list_projects(store)}
    assert set(by_name) == {"Atlas", "Borealis", "Ghost"}
    assert by_name["Atlas"] == {"name": "Atlas", "registered": True, "status": "active",
                                "open_tasks": 2, "decisions": 1, "conversations": 1}
    assert by_name["Ghost"]["registered"] is False and by_name["Ghost"]["open_tasks"] == 1


# ------------------------------------------------------------------ registry


def test_create_update_delete_and_validation(store):
    created = store.projects.create("  Atlas   One ", goal="  Do it  ")
    assert created["name"] == "Atlas One" and created["goal"] == "Do it"
    with pytest.raises(ValueError):
        store.projects.create("atlas one")  # same key
    for bad in ("", "x" * 65):
        with pytest.raises(ValueError):
            store.projects.create(bad)
    with pytest.raises(ValueError):
        store.projects.create("P2", status="done")
    with pytest.raises(ValueError):
        store.projects.create("P3", goal="key sk-abcdefghijklmnopqrstuvwxyz123456")
    assert store.projects.update("ATLAS ONE", status="paused", goal="New goal") == ["goal", "status"]
    assert store.projects.update("atlas one", status="paused") == []
    with pytest.raises(ValueError):
        store.projects.update("Atlas One")
    with pytest.raises(ProjectNotFoundError):
        store.projects.update("missing", goal="x")
    assert store.projects.delete("atlas one") is True
    assert store.projects.delete("atlas one") is False



def test_project_rename_migrates_linked_tasks_decisions_chats_and_milestones(store):
    seed(store)
    milestone_id = store.projects.add_milestone("Atlas", "Atlas migration milestone")

    changed = store.projects.update(
        "Atlas", new_name="Front/Back", goal="Move the importer", status="paused"
    )

    assert changed == ["goal", "name", "status"]
    assert store.projects.get("Atlas") is None
    renamed = store.projects.get("Front/Back")
    assert renamed["name"] == "Front/Back"
    assert renamed["goal"] == "Move the importer" and renamed["status"] == "paused"
    assert store.projects.milestones("Front/Back")[0]["milestone_id"] == milestone_id

    view = ws.workspace(store, "front/back")
    assert view["registered"] is True
    assert {task["title"] for task in view["tasks"]} == {"atlas task", "Atlas task two"}
    assert [decision["title"] for decision in view["decisions"]] == ["Atlas db"]
    assert [chat["conversation_id"] for chat in view["conversations"]] == ["conv-atlas"]
    assert ws.list_projects(store)[0]["name"] == "Borealis"
    assert store.personal.list(kind="task", project="Atlas", limit=20) == []


def test_project_rename_refuses_to_merge_an_existing_or_discovered_name(store):
    store.projects.create("Atlas")
    store.projects.create("Borealis")
    store.personal.add(kind="task", title="unregistered item", project="Ghost")

    with pytest.raises(ValueError, match="workspace for the new project name"):
        store.projects.update("Atlas", new_name="Borealis")
    with pytest.raises(ValueError, match="already used by labelled material"):
        store.projects.update("Atlas", new_name="Ghost")

    assert store.projects.get("Atlas") is not None
    assert store.projects.get("Borealis") is not None
    assert [item["title"] for item in store.personal.list(project="Ghost")] == [
        "unregistered item"
    ]

def test_delete_removes_workspace_metadata_but_not_labelled_work(store):
    seed(store)
    mid = store.projects.add_milestone("Atlas", "Beta")
    assert store.projects.delete("Atlas") is True
    assert store.projects.milestones("Atlas") == []
    with sqlite3.connect(store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM project_milestones").fetchone()[0] == 0
    assert mid
    view = ws.workspace(store, "Atlas")
    assert view["registered"] is False and view["counts"]["open_tasks"] == 2


def test_audit_holds_ids_not_goal_or_titles(store):
    store.projects.create("Atlas", goal="Confidential goal text")
    mid = store.projects.add_milestone("Atlas", "Confidential milestone")
    store.projects.update_milestone("Atlas", mid, status="done")
    store.projects.update("Atlas", goal="Another confidential goal")
    store.projects.delete("Atlas")
    dump = str(store.audit.list(limit=100))
    for text in ("Confidential", "Another confidential"):
        assert text not in dump


# ------------------------------------------------------------------ milestones


def test_milestone_lifecycle_and_validation(store):
    store.projects.create("Atlas")
    with pytest.raises(ProjectNotFoundError):
        store.projects.add_milestone("Nope", "x")
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    a = store.projects.add_milestone("Atlas", "Alpha release", due_at=soon)
    b = store.projects.add_milestone("Atlas", "Beta release")
    for bad in ("", "x" * 201):
        with pytest.raises(ValueError):
            store.projects.add_milestone("Atlas", bad)
    with pytest.raises(ValueError):
        store.projects.add_milestone("Atlas", "t", due_at="2026-12-01")  # naive
    assert store.projects.update_milestone("Atlas", a, status="done") == ["status"]
    done = next(m for m in store.projects.milestones("Atlas") if m["milestone_id"] == a)
    assert done["completed_at"] is not None
    assert store.projects.update_milestone("Atlas", a, status="open") == ["status"]
    with pytest.raises(ValueError):
        store.projects.update_milestone("Atlas", a, status="late")
    assert store.projects.update_milestone("Atlas", b, due_at=soon) == ["due_at"]
    assert store.projects.update_milestone("Atlas", b, clear_due=True) == ["due_at"]
    assert store.projects.delete_milestone("Atlas", b) is True
    assert ws.workspace(store, "Atlas")["counts"]["open_milestones"] == 1


def test_milestone_ids_do_not_cross_projects(store):
    store.projects.create("Atlas")
    store.projects.create("Borealis")
    mid = store.projects.add_milestone("Atlas", "Atlas only")
    with pytest.raises(MilestoneNotFoundError):
        store.projects.update_milestone("Borealis", mid, status="done")
    assert store.projects.delete_milestone("Borealis", mid) is False
    assert store.projects.milestones("Borealis") == []
    assert [m["title"] for m in store.projects.milestones("atlas")] == ["Atlas only"]


# ------------------------------------------------------------------ migration / export


def test_upgrade_from_v7_database_keeps_data(tmp_path):
    path = tmp_path / "old.sqlite3"
    first = MemoryStore(path)
    first.personal.add(kind="task", title="Keep me", project="Atlas")
    db = sqlite3.connect(path)
    try:
        db.execute("DROP TABLE project_milestones")
        db.execute("DROP TABLE projects")
        db.execute("DELETE FROM schema_migrations WHERE version = 8")
        db.commit()
    finally:
        db.close()
    reopened = MemoryStore(path)
    assert [t["title"] for t in ws.workspace(reopened, "Atlas")["tasks"]] == ["Keep me"]
    assert reopened.projects.create("Atlas")["status"] == "active"


def test_export_includes_projects_and_milestones(store):
    store.projects.create("Atlas", goal="G")
    store.projects.add_milestone("Atlas", "M1")
    data = store.export_data()
    assert data["projects"][0]["name"] == "Atlas"
    assert data["project_milestones"][0]["title"] == "M1"


# ------------------------------------------------------------------ API


def test_api_workspace_flow_and_isolation(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    seed(memory)
    assert client.post("/api/projects", json={"name": "atlas"}).status_code == 400  # exists
    created = client.post("/api/projects", json={"name": "Cirrus One", "goal": "Launch"})
    assert created.status_code == 201

    view = client.get("/api/projects/ATLAS").json()
    assert view["counts"]["open_tasks"] == 2 and "Borealis" not in str(view)
    listed = client.get("/api/projects").json()["projects"]
    assert {p["name"] for p in listed} == {"Atlas", "Borealis", "Cirrus One"}

    patched = client.patch("/api/projects/cirrus one", json={"status": "paused"})
    assert patched.status_code == 200 and patched.json()["changed"] == ["status"]
    assert client.patch("/api/projects/missing", json={"status": "paused"}).status_code == 404
    assert client.patch("/api/projects/Atlas", json={"status": "bogus"}).status_code == 400
    assert client.patch("/api/projects/Atlas", json={"bogus": 1}).status_code == 422

    made = client.post("/api/projects/Atlas/milestones", json={"title": "Beta"})
    assert made.status_code == 201
    mid = made.json()["milestone_id"]
    assert client.post("/api/projects/Nope/milestones", json={"title": "x"}).status_code == 404
    assert client.patch(f"/api/projects/Borealis/milestones/{mid}", json={"status": "done"}
                        ).status_code == 404
    assert client.patch(f"/api/projects/Atlas/milestones/{mid}", json={"status": "done"}
                        ).json() == {"changed": ["status"]}
    assert client.delete(f"/api/projects/Borealis/milestones/{mid}").status_code == 404
    assert client.delete(f"/api/projects/Atlas/milestones/{mid}").status_code == 200

    assert client.delete("/api/projects/cirrus one").status_code == 200
    assert client.delete("/api/projects/cirrus one").status_code == 404


def test_api_supports_slash_names_for_workspace_milestones_and_rename(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    created = client.post(
        "/api/projects", json={"name": "Front/Back", "goal": "Build the site map"}
    )
    assert created.status_code == 201
    memory.personal.add(kind="task", title="route task", project="Front/Back")
    memory.structured.add(
        record_type="decision", title="routing choice",
        body="Use a shared path resolver.", project="Front/Back",
    )
    memory.ensure_conversation("conv-front-back-01")
    memory.append_message("conv-front-back-01", "user", "Plan the routes.")
    memory.set_conversation_project("conv-front-back-01", "Front/Back")

    made = client.post("/api/projects/Front%2FBack/milestones", json={"title": "Route tests"})
    assert made.status_code == 201, made.text
    milestone_id = made.json()["milestone_id"]
    view = client.get("/api/projects/Front%2FBack")
    assert view.status_code == 200, view.text
    assert view.json()["counts"] == {
        "open_tasks": 1, "decisions": 1, "conversations": 1, "open_milestones": 1
    }

    renamed = client.patch(
        "/api/projects/Front%2FBack",
        json={"name": "Routes/Phase 2", "status": "paused"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["project"]["name"] == "Routes/Phase 2"
    assert renamed.json()["changed"] == ["name", "status"]
    assert client.get("/api/projects/Front%2FBack").status_code == 404
    after = client.get("/api/projects/Routes%2FPhase%202")
    assert after.status_code == 200, after.text
    assert after.json()["tasks"][0]["title"] == "route task"
    assert client.patch(
        f"/api/projects/Routes%2FPhase%202/milestones/{milestone_id}",
        json={"status": "done"},
    ).json() == {"changed": ["status"]}
