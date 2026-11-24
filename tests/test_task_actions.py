"""Conversational task actions on the shared task store (MIA-LIFE-03, Brain half)."""

from datetime import UTC, datetime, timedelta

import pytest

from mia.brain.task_actions import MAX_BULK, TaskActions
from mia.memory.store import MemoryStore

from .test_brain_pipeline import Scripted, chat, client_for

CID = "conv-task-actions-1"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    memory.ensure_conversation(CID)
    clock = Clock()
    return memory, TaskActions(memory, clock=clock), clock


def say(actions, text, cid=CID):
    return actions.handle(cid, "msg-1", text)


def titles(memory, status="open"):
    return sorted(t["title"] for t in memory.personal.list(kind="task", status=status))


def add(memory, title, **kw):
    return memory.personal.add(kind="task", title=title, **kw)


# ------------------------------------------------------------------ non-commands


@pytest.mark.parametrize("text", [
    "", "hello there", "I have a task for you, write me a poem", "Tell me about task scheduling",
    "mark the meeting as done", "yes", "no", "show me the tasks in this repo",
    "list my tasks for the week", "what is a task queue?", "x" * 700,
    "delete the file report.txt", "add 2 and 3",
])
def test_ordinary_chat_is_never_hijacked(env, text):
    memory, actions, _ = env
    add(memory, "meeting")
    assert say(actions, text) is None
    assert titles(memory) == ["meeting"]


# ------------------------------------------------------------------ add


def test_add_task_records_provenance_and_actor(env):
    memory, actions, _ = env
    out = actions.handle(CID, "msg-42", "Add a task: buy milk")
    assert out["action"] == "add" and out["changed"] == 1 and "Added task \"buy milk\"" in out["reply"]
    item = memory.personal.get(out["item_ids"][0])
    assert item["provenance"] == "owner_provided" and item["private"] is False
    assert (item["source_conversation_id"], item["source_message_id"]) == (CID, "msg-42")
    actors = {e["actor"] for e in memory.audit.list(target_type="personal_item", limit=20)}
    assert "brain" in actors


def test_add_parses_due_priority_project_in_owner_timezone(env):
    memory, actions, _ = env
    memory.settings.update({"owner_timezone": "Asia/Karachi"})
    out = say(actions, "please add a task call Sam due 2026-12-05 high priority in project Home")
    item = memory.personal.get(out["item_ids"][0])
    assert item["title"] == "call Sam" and item["priority"] == 1 and item["project"] == "Home"
    assert item["due_at"] == "2026-12-05T18:59:00+00:00"  # 23:59 Karachi (UTC+5)
    timed = say(actions, "create a new task: pay rent due 2026-12-01 09:30")
    assert memory.personal.get(timed["item_ids"][0])["due_at"] == "2026-12-01T04:30:00+00:00"


def test_add_to_my_list_form_and_validation(env):
    memory, actions, _ = env
    assert say(actions, "add buy eggs to my task list")["changed"] == 1
    assert titles(memory) == ["buy eggs"]
    assert say(actions, "add a task due 2026-13-45")["action"] == "error"  # empty title after parsing
    assert "not valid" in say(actions, "add a task: x due 2026-13-45")["reply"]
    assert titles(memory) == ["buy eggs"]


def test_add_duplicate_and_credential_are_refused_without_echo(env):
    memory, actions, _ = env
    say(actions, "add a task: pay rent")
    dup = say(actions, "add a task: Pay  Rent")
    assert dup["action"] == "duplicate" and dup["changed"] == 0 and len(titles(memory)) == 1
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    out = say(actions, f"add a task: use key {secret}")
    assert out["action"] == "error" and secret not in out["reply"] and len(titles(memory)) == 1


# ------------------------------------------------------------------ list


def test_list_filters_and_empty_state(env):
    memory, actions, _ = env
    assert "no open tasks" in say(actions, "show my tasks")["reply"]
    a = add(memory, "alpha", project="Atlas", priority=1)
    add(memory, "late", due_at=(datetime.now(UTC) - timedelta(days=1)).isoformat())
    done = add(memory, "finished one")
    memory.personal.complete(done)
    out = say(actions, "What are my open tasks?")
    assert out["action"] == "list" and "alpha" in out["reply"] and f"[{a[:8]}]" in out["reply"]
    assert "finished one" not in out["reply"]
    assert "late" in say(actions, "list my overdue tasks")["reply"]
    assert "alpha" not in say(actions, "list my overdue tasks")["reply"]
    assert "finished one" in say(actions, "show my done tasks")["reply"]
    proj = say(actions, "show my tasks in project Atlas")["reply"]
    assert "alpha" in proj and "late" not in proj


def test_list_is_capped(env):
    memory, actions, _ = env
    for n in range(12):
        add(memory, f"task number {n}")
    reply = say(actions, "list my tasks")["reply"]
    assert "(12)" in reply and "and 4 more" in reply


# ------------------------------------------------------------------ complete / priority


def test_complete_by_title_id_and_bare_form(env):
    memory, actions, _ = env
    a = add(memory, "write report")
    b = add(memory, "water plants")
    c = add(memory, "call dentist")
    assert say(actions, "mark task write report as done")["action"] == "complete"
    assert memory.personal.get(a)["status"] == "done"
    assert say(actions, f"complete task {b[:8]}")["changed"] == 1
    assert say(actions, "mark call dentist as done")["changed"] == 1  # unambiguous, no 'task' word
    assert titles(memory) == [] and memory.personal.get(c)["status"] == "done"
    again = say(actions, "complete task write report")
    assert again["action"] == "not_found"  # finished tasks are not open matches


def test_complete_ambiguous_and_missing_change_nothing(env):
    memory, actions, _ = env
    add(memory, "email Sam about budget")
    add(memory, "email Ann about budget")
    out = say(actions, "complete task email")
    assert out["action"] == "ambiguous" and out["changed"] == 0 and "Sam" in out["reply"]
    assert titles(memory) == ["email Ann about budget", "email Sam about budget"]
    assert say(actions, "complete task nonexistent")["action"] == "not_found"
    assert say(actions, "mark the nonexistent thing as done") is None


def test_complete_recurring_task_reports_next(env):
    memory, actions, _ = env
    due = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    add(memory, "water plants", due_at=due, recurrence={"freq": "daily"})
    out = say(actions, "complete task water plants")
    assert "The next one is due" in out["reply"] and len(titles(memory)) == 1


def test_set_priority(env):
    memory, actions, _ = env
    iid = add(memory, "write report")
    assert say(actions, "set task write report to high priority")["changed"] == 1
    assert memory.personal.get(iid)["priority"] == 1
    assert say(actions, "make task write report low priority")["changed"] == 1
    assert memory.personal.get(iid)["priority"] == 3
    same = say(actions, "set task write report to low")
    assert same["changed"] == 0 and "already" in same["reply"]


# ------------------------------------------------------------------ confirmation


def test_single_delete_needs_explicit_yes(env):
    memory, actions, _ = env
    iid = add(memory, "write report")
    req = say(actions, "delete task write report")
    assert req["needs_confirmation"] and memory.personal.get(iid) is not None
    assert "Reply \"yes\"" in req["reply"] and "Nothing has been changed yet" in req["reply"]
    done = say(actions, "yes")
    assert done["action"] == "confirmed" and done["changed"] == 1 and memory.personal.get(iid) is None
    assert say(actions, "yes") is None  # nothing pending any more


def test_no_and_other_messages_cancel_and_stale_yes_does_nothing(env):
    memory, actions, _ = env
    iid = add(memory, "write report")
    say(actions, "delete task write report")
    assert say(actions, "no")["action"] == "cancelled" and memory.personal.get(iid)
    say(actions, "delete task write report")
    assert say(actions, "what time is it?") is None  # unrelated message cancels it silently
    assert say(actions, "yes") is None and memory.personal.get(iid) is not None


def test_confirmation_expires_and_is_per_conversation(env):
    memory, actions, clock = env
    iid = add(memory, "write report")
    say(actions, "delete task write report")
    memory.ensure_conversation("conv-other-1234")
    assert say(actions, "yes", cid="conv-other-1234") is None  # another chat cannot confirm it
    clock.t += 601
    assert say(actions, "yes") is None and memory.personal.get(iid) is not None
    say(actions, "delete task write report")
    clock.t += 10
    assert say(actions, "yes")["changed"] == 1


def test_bulk_delete_finished_states_and_sample(env):
    memory, actions, _ = env
    keep = add(memory, "still open")
    d1, d2, c1 = add(memory, "done one"), add(memory, "done two"), add(memory, "cancelled one")
    for i in (d1, d2):
        memory.personal.complete(i)
    memory.personal.update(c1, status="cancelled")
    req = say(actions, "delete all my completed tasks")
    assert "2 completed tasks" in req["reply"] and "done one" in req["reply"] and req["needs_confirmation"]
    assert len(memory.personal.list(kind="task")) == 4  # nothing yet
    assert say(actions, "yes")["changed"] == 2
    assert memory.personal.get(c1) and memory.personal.get(keep)
    req2 = say(actions, "clear my finished tasks")
    assert "1 finished tasks" in req2["reply"]
    say(actions, "confirm")
    assert titles(memory, "cancelled") == [] and titles(memory) == ["still open"]


def test_bulk_complete_overdue_and_project_scope(env):
    memory, actions, _ = env
    past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    a = add(memory, "late atlas", due_at=past, project="Atlas")
    b = add(memory, "late borealis", due_at=past, project="Borealis")
    c = add(memory, "not late")
    req = say(actions, "mark all my overdue tasks in project Atlas as done")
    assert "1 overdue tasks in project Atlas" in req["reply"] and "borealis" not in req["reply"]
    assert say(actions, "yes")["changed"] == 1
    assert memory.personal.get(a)["status"] == "done"
    assert memory.personal.get(b)["status"] == "open" and memory.personal.get(c)["status"] == "open"
    assert say(actions, "mark all open tasks done")["needs_confirmation"]


def test_delete_all_tasks_and_nothing_matches(env):
    memory, actions, _ = env
    assert say(actions, "delete all my completed tasks")["action"] == "nothing_to_do"
    add(memory, "one")
    add(memory, "two", project="Atlas")
    req = say(actions, "delete all tasks in project Atlas")
    assert "1 tasks (open and finished) in project Atlas" in req["reply"]
    say(actions, "yes")
    assert titles(memory) == ["one"]


def test_confirmation_only_touches_the_ids_shown_and_still_matching(env):
    memory, actions, _ = env
    d1, d2 = add(memory, "done one"), add(memory, "done two")
    for i in (d1, d2):
        memory.personal.complete(i)
    say(actions, "delete all my done tasks")
    d3 = add(memory, "done three")
    memory.personal.complete(d3)  # finished AFTER the request: must survive
    memory.personal.delete(d2)    # already gone at confirmation
    out = say(actions, "yes")
    assert out["changed"] == 1 and "1 no longer matched" in out["reply"]
    assert memory.personal.get(d3) is not None and memory.personal.get(d1) is None


def test_bulk_over_the_limit_is_refused(env):
    memory, actions, _ = env
    for n in range(MAX_BULK + 1):
        memory.personal.add(kind="task", title=f"task {n}", allow_duplicate=True)
    out = say(actions, "delete all tasks")
    assert out["action"] == "too_many" and not out.get("needs_confirmation")
    assert say(actions, "yes") is None and len(memory.personal.list(kind="task", limit=500)) == MAX_BULK + 1


# ------------------------------------------------------------------ privacy and temporary chats


def test_private_tasks_are_invisible_to_chat(env):
    memory, actions, _ = env
    secret = add(memory, "private errand zebra", private=True)
    add(memory, "public errand")
    listing = say(actions, "show my tasks")
    assert "zebra" not in listing["reply"] and "1 private task(s) are not shown" in listing["reply"]
    for text in ("complete task private errand zebra", f"complete task {secret[:8]}",
                 "delete task zebra", "set task zebra to high priority"):
        out = say(actions, text)
        assert out["action"] == "not_found" and "zebra" not in out["reply"]
    assert memory.personal.get(secret)["status"] == "open"
    req = say(actions, "delete all tasks")
    assert "zebra" not in req["reply"] and "Private tasks are never included" in req["reply"]
    say(actions, "yes")
    assert memory.personal.get(secret) is not None and titles(memory) == ["private errand zebra"]


def test_a_task_made_private_before_confirmation_is_left_alone(env):
    memory, actions, _ = env
    iid = add(memory, "write report")
    say(actions, "delete task write report")
    memory.personal.update(iid, private=True)
    out = say(actions, "yes")
    assert out["changed"] == 0 and memory.personal.get(iid) is not None


def test_temporary_chat_is_read_only(env):
    memory, actions, _ = env
    memory.ensure_conversation("conv-temp-1234")
    memory.set_conversation_temporary("conv-temp-1234", True)
    add(memory, "write report")
    for text in ("add a task: buy milk", "complete task write report", "delete task write report",
                 "delete all tasks", "set task write report to high priority"):
        out = say(actions, text, cid="conv-temp-1234")
        assert out["action"] == "refused_temporary" and out["changed"] == 0
    assert titles(memory) == ["write report"]
    assert "write report" in say(actions, "show my tasks", cid="conv-temp-1234")["reply"]


# ------------------------------------------------------------------ Brain integration


def test_chat_task_command_skips_the_model_and_shares_the_api_store(tmp_path, monkeypatch):
    provider = Scripted(reply="model reply")
    client, memory, _ = client_for(tmp_path, monkeypatch, provider)
    cid = "conv-brain-tasks-1"
    out = chat(client, "Add a task: write report", conversation_id=cid).json()
    assert out["content"].startswith("Added task \"write report\"")
    assert (out["provider_id"], out["model_id"]) == ("mia-brain", "task-action")
    assert out["task_action"]["action"] == "add" and "reply" not in out["task_action"]
    assert provider.seen == []  # no model call
    api = client.get("/api/personal/items").json()["items"]
    assert [i["title"] for i in api] == ["write report"] and api[0]["source_conversation_id"] == cid
    roles = [m["role"] for m in memory.get_messages(cid, limit=10)]
    assert roles == ["user", "assistant"]

    # a task created through the API is visible to chat, and completing in chat updates the API
    client.post("/api/personal/items", json={"kind": "task", "title": "water plants"})
    listing = chat(client, "show my tasks", conversation_id=cid).json()["content"]
    assert "write report" in listing and "water plants" in listing
    chat(client, "mark task water plants done", conversation_id=cid)
    statuses = {i["title"]: i["status"] for i in client.get("/api/personal/items").json()["items"]}
    assert statuses == {"write report": "open", "water plants": "done"}
    assert client.get("/api/brain/diagnostics").json()["turns"][0]["outcome"] == "task_action"


def test_chat_bulk_delete_needs_confirmation_over_two_turns(tmp_path, monkeypatch):
    provider = Scripted(reply="model reply")
    client, memory, _ = client_for(tmp_path, monkeypatch, provider)
    cid = "conv-brain-tasks-2"
    for title in ("a one", "b two"):
        i = memory.personal.add(kind="task", title=title)
        memory.personal.complete(i)
    first = chat(client, "delete all my finished tasks", conversation_id=cid).json()
    assert first["task_action"]["needs_confirmation"] is True
    assert len(client.get("/api/personal/items").json()["items"]) == 2
    second = chat(client, "yes", conversation_id=cid).json()
    assert second["task_action"]["changed"] == 2 and client.get("/api/personal/items").json()["items"] == []
    assert provider.seen == []


def test_streaming_task_command_and_normal_chat_still_reaches_the_model(tmp_path, monkeypatch):
    provider = Scripted(reply="model reply", stream_parts=["Hel", "lo"])
    client, memory, _ = client_for(tmp_path, monkeypatch, provider)
    with client.stream("POST", "/api/gateway/chat/stream",
                       json={"prompt": "add a task: stream test", "conversation_id": "conv-brain-tasks-3",
                             "use_tools": False}) as r:
        body = "".join(r.iter_text())
    assert "event: delta" in body and "Added task" in body and "event: done" in body
    assert provider.seen == [] and [t["title"] for t in memory.personal.list(kind="task")] == ["stream test"]
    normal = chat(client, "Tell me about Mia", conversation_id="conv-brain-tasks-4").json()
    assert normal["content"] == "model reply" and len(provider.seen) == 1
    assert normal["task_action"] is None


def test_hook_failure_falls_back_to_normal_chat(tmp_path, monkeypatch):
    provider = Scripted(reply="model reply")
    client, memory, app = client_for(tmp_path, monkeypatch, provider)
    app.state.brain.task_actions = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
    out = chat(client, "add a task: x", conversation_id="conv-brain-tasks-5").json()
    assert out["content"] == "model reply" and memory.personal.list(kind="task") == []


def test_bulk_complete_skips_tasks_finished_after_the_request(env):
    memory, actions, _ = env
    a, b = add(memory, "first open"), add(memory, "second open")
    say(actions, "mark all open tasks done")
    memory.personal.complete(a)  # finished by someone else before the owner said yes
    out = say(actions, "yes")
    assert out["changed"] == 1 and "1 no longer matched" in out["reply"]
    assert memory.personal.get(b)["status"] == "done"


def test_a_long_message_is_conversation_not_a_command(env):
    memory, actions, _ = env
    assert say(actions, "add a task: " + "x" * 700) is None
    assert memory.personal.list(kind="task") == []


def test_list_shows_at_most_eight_lines(env):
    memory, actions, _ = env
    for n in range(12):
        add(memory, f"task number {n}")
    lines = [ln for ln in say(actions, "list my tasks")["reply"].splitlines() if ln.startswith("- ")]
    assert len(lines) == 8
