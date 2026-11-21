"""Project-scoped conversation continuity (MIA-BRAIN-03).

Owner-approved behaviour: a conversation may be assigned to a project (opt-in; the
default is no project). Recall for a conversation in a project prefers that project's
history, then unassigned chats, and never another project's chats. Temporary chats stay
excluded from all recall.
"""

import sqlite3

import pytest

from mia.brain.context import (
    _prior_conversation_tail,
    build_context,
    has_persistent_prior_conversation,
)
from mia.brain.intent import analyze_intent
from mia.gateway.contracts import ModelCandidate
from mia.memory.schema import LATEST_VERSION
from mia.memory.store import MemoryStore

from .test_brain_pipeline import Scripted, chat, client_for


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.sqlite3")


def convo(store, cid, text, *, project=None, temporary=False):
    store.ensure_conversation(cid)
    store.append_message(cid, "user", text)
    if project is not None:
        assert store.set_conversation_project(cid, project)
    if temporary:
        store.set_conversation_temporary(cid, True)
    return cid


def tail_text(store, current):
    return " ".join(m["content"] for m in _prior_conversation_tail(store, current))


# ------------------------------------------------------------------ store


def test_project_defaults_to_none_and_can_be_set_and_cleared(store):
    cid = convo(store, "conv-default-1", "hello there")
    assert store.get_conversation(cid)["project"] is None

    assert store.set_conversation_project(cid, "Atlas")
    assert store.get_conversation(cid)["project"] == "Atlas"
    assert [c["project"] for c in store.list_conversations()] == ["Atlas"]

    assert store.set_conversation_project(cid, None)
    assert store.get_conversation(cid)["project"] is None


def test_project_name_is_normalised_and_validated(store):
    cid = convo(store, "conv-valid-001", "hello there")
    assert store.set_conversation_project(cid, "  Atlas   migration \n")
    assert store.get_conversation(cid)["project"] == "Atlas migration"
    for bad in ("", "   ", "x" * 65, "bad\x00name", "bad\x07name"):
        with pytest.raises(ValueError):
            store.set_conversation_project(cid, bad)
    assert store.get_conversation(cid)["project"] == "Atlas migration"  # unchanged by failures


def test_unknown_conversation_returns_false_and_change_is_audited(store):
    assert store.set_conversation_project("does-not-exist", "Atlas") is False
    cid = convo(store, "conv-audit-001", "hello there")
    store.set_conversation_project(cid, "Atlas")
    with sqlite3.connect(store.database_path) as db:
        actions = [r[0] for r in db.execute("SELECT action FROM memory_audit_log")]
    assert "conversation_project_set" in actions


def test_existing_database_gains_the_project_column(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    MemoryStore(path)
    with sqlite3.connect(path) as db:  # simulate a database from before this feature
        db.execute("ALTER TABLE conversations DROP COLUMN project")
        db.execute("DELETE FROM schema_migrations WHERE version = ?", (LATEST_VERSION,))
    reopened = MemoryStore(path)
    cid = convo(reopened, "conv-legacy-01", "hello there", project="Atlas")
    assert reopened.get_conversation(cid)["project"] == "Atlas"


# ------------------------------------------------------------------ recall


def test_recall_prefers_the_current_projects_history(store):
    convo(store, "alpha-old-chat", "Alpha: we chose the Cedar schema.", project="Alpha")
    convo(store, "beta-new-chat", "Beta: totally unrelated Beta secret.", project="Beta")
    convo(store, "free-newest-chat", "Unassigned musings.")
    current = convo(store, "alpha-current", "Continue where we stopped.", project="Alpha")

    text = tail_text(store, current)
    assert "Cedar" in text and "Beta" not in text and "Unassigned" not in text


def test_project_match_ignores_case_and_spacing(store):
    convo(store, "alpha-old-chat", "Alpha: Cedar schema decided.", project="Alpha")
    convo(store, "beta-new-chat", "Beta: other work.", project="Beta")
    current = convo(store, "alpha-current", "Continue.", project="  alpha ")
    assert "Cedar" in tail_text(store, current)


def test_falls_back_to_unassigned_but_never_to_another_project(store):
    convo(store, "free-older-chat", "Unassigned: general notes.")
    convo(store, "beta-new-chat", "Beta: private Beta detail.", project="Beta")  # newest
    current = convo(store, "alpha-current", "Continue.", project="Alpha")

    text = tail_text(store, current)
    assert "general notes" in text and "Beta" not in text


def test_project_conversation_with_only_other_projects_has_no_prior_context(store):
    convo(store, "beta-only-chat", "Beta: private Beta detail.", project="Beta")
    current = convo(store, "alpha-current", "Continue where we stopped.", project="Alpha")

    assert not has_persistent_prior_conversation(store, current)
    assert tail_text(store, current) == ""
    assert analyze_intent("Continue where we stopped.", has_prior_context=False).needs_clarification


def test_conversation_without_a_project_keeps_the_previous_behaviour(store):
    convo(store, "alpha-old-chat", "Alpha: Cedar decided.", project="Alpha")
    convo(store, "beta-new-chat", "Beta: newest persistent chat.", project="Beta")
    current = convo(store, "free-current", "Continue.")
    assert "newest persistent chat" in tail_text(store, current)


def test_temporary_chat_in_the_same_project_stays_excluded(store):
    convo(store, "alpha-old-chat", "Alpha: Cedar decided.", project="Alpha")
    convo(store, "alpha-temp-chat", "Alpha: temporary cobalt-otter.", project="Alpha", temporary=True)
    current = convo(store, "alpha-current", "Continue.", project="Alpha")

    text = tail_text(store, current)
    assert "Cedar" in text and "cobalt-otter" not in text


def test_build_context_uses_the_project_history_end_to_end(store):
    convo(store, "alpha-old-chat", "Alpha: we chose the Cedar schema.", project="Alpha")
    convo(store, "beta-new-chat", "Beta: unrelated Beta secret.", project="Beta")
    current = convo(store, "alpha-current", "Continue where we stopped.", project="Alpha")
    cand = ModelCandidate("p", "m", frozenset({"chat"}), 16384, True, True, True, data_local=True)

    built = build_context(
        store, identity_text="You are Mia.", prompt="Continue where we stopped.",
        conversation_id=current,
        analysis=analyze_intent("Continue where we stopped.",
                                has_prior_context=has_persistent_prior_conversation(store, current)),
        candidates=[cand], required_capabilities=frozenset(),
        recent_messages=store.get_messages(current, limit=12),
    )
    content = " ".join(m["content"] for m in built.messages)
    assert "Cedar" in content and "Beta secret" not in content


# ------------------------------------------------------------------ API


def test_project_api_sets_lists_clears_and_validates(tmp_path, monkeypatch):
    client, _memory, _ = client_for(tmp_path, monkeypatch, Scripted(reply="ok"))
    chat(client, "Tell me about the plan", conversation_id="conv-api-project-1")

    r = client.post("/api/conversations/conv-api-project-1/project", json={"project": "Atlas"})
    assert r.status_code == 200 and r.json()["project"] == "Atlas"
    listed = client.get("/api/conversations").json()["items"]
    assert [c["project"] for c in listed] == ["Atlas"]
    assert client.get("/api/conversations/conv-api-project-1").json()["project"] == "Atlas"

    assert client.post("/api/conversations/conv-api-project-1/project",
                       json={"project": "x" * 65}).status_code == 400
    assert client.post("/api/conversations/missing-conv-1/project",
                       json={"project": "Atlas"}).status_code == 404

    cleared = client.post("/api/conversations/conv-api-project-1/project", json={"project": None})
    assert cleared.status_code == 200 and cleared.json()["project"] is None
