"""Bounded proactive suggestions about stalled work (MIA-LIFE-14). Fixed clock, real stores."""

from datetime import UTC, datetime, timedelta

import pytest

from mia.life.store import LifeStore
from mia.life.suggestions import run_suggestions
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for

# The life store stamps outreach with the real clock, so the tests use a real "now"
# (fixed once per run); every other time in the tests is relative to it.
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def stores(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    life = LifeStore(tmp_path / "life.sqlite3")
    life.update_settings({"outreach_enabled": True, "min_outreach_gap_minutes": 0,
                          "max_outreach_per_day": 10})
    return memory, life


def age(memory, table, key_col, key, days, *, touched=True):
    """Make a row look `days` old; touched=False keeps created_at == updated_at."""
    when = (NOW - timedelta(days=days)).isoformat()
    with memory._connection() as db:
        if table == "personal_items" and not touched:
            db.execute("UPDATE personal_items SET created_at = ?, updated_at = ? WHERE item_id = ?",
                       (when, when, key))
        elif table == "personal_items":
            db.execute("UPDATE personal_items SET created_at = ?, updated_at = ? WHERE item_id = ?",
                       ((NOW - timedelta(days=days + 5)).isoformat(), when, key))
        else:
            db.execute(f"UPDATE {table} SET updated_at = ? WHERE {key_col} = ?", (when, key))  # noqa: S608


def task(memory, title, idle=20, **kw):
    iid = memory.personal.add(kind="task", title=title, **kw)
    age(memory, "personal_items", "item_id", iid, idle)
    return iid


def reasons(report):
    return {s["topic_key"].split(":", 1)[1]: s["reason"] for s in report["suppressed"]}


def run(stores, **kw):
    memory, life = stores
    kw.setdefault("dry_run", False)
    kw.setdefault("now", datetime.now(UTC))  # fresh per run, like production
    return run_suggestions(memory, life, **kw)


# ------------------------------------------------------------------ candidates


def test_dry_run_is_default_reports_and_sends_nothing(stores):
    memory, life = stores
    iid = task(memory, "Plan the offsite")
    report = run_suggestions(memory, life, now=NOW)
    assert report["dry_run"] is True and report["sent"] == []
    only = report["would_send"][0]
    assert only["evidence"] == [{"type": "personal_item", "ref": iid}]
    assert "20 days" in only["body"] and "Plan the offsite" in only["title"]
    assert life.list_outreach() == []


def test_idle_task_is_sent_with_evidence_and_rationale(stores):
    memory, life = stores
    iid = task(memory, "Plan the offsite", priority=1)
    report = run(stores)
    assert len(report["sent"]) == 1
    msg = life.get_outreach(report["sent"][0]["message_id"])
    assert msg["kind"] == "task_check_in" and msg["state"] == "pending"
    assert msg["topic_key"] == f"stalled-task:{iid}"
    assert msg["evidence"] == [{"type": "personal_item", "ref": iid}]
    assert "no change for 20 days" in msg["body"]


def test_not_candidates_fresh_due_overdue_private_and_finished(stores):
    memory, life = stores
    task(memory, "fresh", idle=3)
    task(memory, "overdue", due_at=(NOW - timedelta(days=2)).isoformat())
    task(memory, "private", private=True)
    done = task(memory, "done")
    memory.personal.complete(done)
    age(memory, "personal_items", "item_id", done, 30)
    future = task(memory, "due later", due_at=(NOW + timedelta(days=5)).isoformat())
    report = run(stores, dry_run=True)
    assert [c["topic_key"] for c in report["would_send"]] == [f"stalled-task:{future}"]
    assert report["evaluated"] == 1
    assert "private" not in str(report) and "overdue" not in str(report)


def test_idle_goal_is_suggested_only_when_active_with_goal_and_no_activity(stores):
    memory, life = stores
    for name, goal, status in (("Old", "Finish the book", "active"), ("Fresh", "Ship app", "active"),
                               ("Paused", "A goal", "paused"), ("NoGoal", "", "active")):
        memory.projects.create(name, goal=goal, status=status)
        age(memory, "projects", "project_key", name.casefold(), 25)
    memory.personal.add(kind="task", title="recent", project="Fresh")  # activity today
    report = run(stores, dry_run=True, max_per_run=3)
    assert [c["topic_key"] for c in report["would_send"]] == ["stalled-goal:old"]
    assert report["evaluated"] == 1  # paused, goal-less and recently active projects are not candidates
    body = report["would_send"][0]["body"]
    assert "Finish the book" in body and "25 days" in body
    assert report["would_send"][0]["evidence"] == [{"type": "project", "ref": "old"}]


def test_candidates_are_ranked_by_relevance(stores):
    memory, life = stores
    low = task(memory, "normal one", idle=20)
    high = task(memory, "urgent one", idle=20, priority=1)
    report = run(stores, dry_run=True, max_per_run=3)
    assert [c["topic_key"] for c in report["would_send"]] == [
        f"stalled-task:{high}", f"stalled-task:{low}"]
    assert report["would_send"][0]["relevance"] > report["would_send"][1]["relevance"]


# ------------------------------------------------------------------ suppression


def test_stale_low_confidence_low_relevance_and_duplicate(stores):
    memory, life = stores
    ancient = task(memory, "ancient", idle=120)
    ghost = task(memory, "captured never confirmed", idle=20, provenance="conversation_captured",
                 source_conversation_id="conv-1", source_message_id="msg-1")
    age(memory, "personal_items", "item_id", ghost, 20, touched=False)
    confirmed = task(memory, "captured and confirmed", idle=20, provenance="conversation_captured",
                     source_conversation_id="conv-1", source_message_id="msg-2")
    meh = task(memory, "low priority", idle=14, priority=3)
    first = task(memory, "Same title", idle=30)
    second = task(memory, "same  title", idle=20, allow_duplicate=True)
    report = run(stores)
    got = reasons(report)
    assert got[ancient] == "stale" and got[ghost] == "low_confidence"
    assert got[meh] == "low_relevance" and got[second] == "duplicate"
    sent = {s["topic_key"] for s in report["sent"]}
    assert f"stalled-task:{first}" in sent or f"stalled-task:{confirmed}" in sent
    assert f"stalled-task:{second}" not in sent
    memory_min = life.update_settings({"suggestion_min_relevance_pct": 0})
    assert memory_min.suggestion_min_relevance_pct == 0
    assert reasons(run(stores, dry_run=True, max_per_run=3)).get(meh) != "low_relevance"


# ------------------------------------------------------------------ shared policy


def test_outreach_is_opt_in_and_emergency_stop_blocks_everything(stores):
    memory, life = stores
    iid = task(memory, "Plan")
    life.update_settings({"outreach_enabled": False})
    assert reasons(run(stores))[iid] == "outreach_disabled"
    life.update_settings({"outreach_enabled": True})
    life.update_settings({"emergency_stop": True})
    report = run(stores)
    assert reasons(report)[iid] == "emergency_stop" and report["sent"] == []
    assert life.list_outreach() == []


def test_quiet_hours_cap_gap_and_backlog(stores):
    memory, life = stores
    ids = [task(memory, f"task {n}", idle=20 + n) for n in range(4)]
    minute = NOW.hour * 60 + NOW.minute
    life.update_settings({"quiet_hours_enabled": True,
                          "quiet_start_minute": (minute - 30) % 1440,
                          "quiet_end_minute": (minute + 30) % 1440})  # window contains NOW
    assert set(reasons(run(stores)).values()) == {"quiet_hours"}
    life.update_settings({"quiet_hours_enabled": False, "max_outreach_per_day": 1})
    first = run(stores)
    assert len(first["sent"]) == 1
    assert "daily_outreach_cap" in set(reasons(run(stores)).values())
    life.update_settings({"max_outreach_per_day": 10, "min_outreach_gap_minutes": 600})
    assert "outreach_gap" in set(reasons(run(stores)).values())
    life.update_settings({"min_outreach_gap_minutes": 0, "max_unanswered_outreach": 1})
    assert "unanswered_backlog" in set(reasons(run(stores)).values())
    assert ids


def test_dismissed_topic_and_cooldown_prevent_repeats(stores):
    memory, life = stores
    iid = task(memory, "Plan")
    message_id = run(stores)["sent"][0]["message_id"]
    life.update_settings({"max_unanswered_outreach": 50})
    assert reasons(run(stores))[iid] == "topic_cooldown"  # sent a moment ago
    life.set_outreach_state(message_id, "dismissed")
    assert reasons(run(stores))[iid] == "topic_dismissed"


def test_max_per_run_limits_and_validates(stores):
    memory, life = stores
    for n in range(3):
        task(memory, f"task {n}", idle=20 + n)
    report = run(stores, max_per_run=2)
    assert len(report["sent"]) == 2 and "run_limit" in set(reasons(report).values())
    for bad in (0, 4, True, "1"):
        with pytest.raises(ValueError):
            run_suggestions(*stores, now=NOW, max_per_run=bad)


# ------------------------------------------------------------------ withdrawal and opt-out


def test_pending_suggestion_is_withdrawn_when_its_evidence_changes(stores):
    memory, life = stores
    iid = task(memory, "Plan the offsite")
    message_id = run(stores)["sent"][0]["message_id"]
    assert life.unanswered_outreach() == 1
    memory.personal.complete(iid)
    after = run(stores)
    assert after["withdrawn"] == [message_id]
    assert life.get_outreach(message_id)["state"] == "withdrawn"
    assert life.unanswered_outreach() == 0


def test_touching_a_task_or_goal_withdraws_its_pending_suggestion(stores):
    memory, life = stores
    memory.projects.create("Atlas", goal="Ship it")
    age(memory, "projects", "project_key", "atlas", 25)
    iid = task(memory, "Write docs")
    got = run(stores, max_per_run=2)
    assert len(got["sent"]) == 2
    # the owner edits both after the messages were created
    memory.personal.update(iid, details="more detail")
    memory.projects.update("Atlas", goal="Ship it fast")
    withdrawn = run_suggestions(memory, life, now=datetime.now(UTC) + timedelta(seconds=5),
                                dry_run=False)["withdrawn"]
    assert len(withdrawn) == 2


def test_opt_out_withdraws_everything_and_sends_nothing(stores):
    memory, life = stores
    task(memory, "Plan")
    message_id = run(stores)["sent"][0]["message_id"]
    life.update_settings({"suggestions_enabled": False})
    report = run(stores)
    assert report["disabled"] == "suggestions_disabled" and report["sent"] == []
    assert report["withdrawn"] == [message_id]
    assert life.get_outreach(message_id)["state"] == "withdrawn"


def test_emergency_stop_withdraws_pending_suggestions(stores):
    memory, life = stores
    task(memory, "Plan")
    message_id = run(stores)["sent"][0]["message_id"]
    life.update_settings({"emergency_stop": True})
    assert run(stores)["withdrawn"] == [message_id]


def test_other_outreach_kinds_are_never_withdrawn(stores):
    memory, life = stores
    other = life.add_outreach(kind="finding", topic_key="finding:abc", title="t", body="b",
                              evidence=[{"type": "cycle", "ref": "c"}])
    life.update_settings({"suggestions_enabled": False})
    assert run(stores)["withdrawn"] == []
    assert life.get_outreach(other)["state"] == "pending"


# ------------------------------------------------------------------ determinism and settings


def test_report_is_deterministic(stores):
    memory, life = stores
    for n in range(3):
        task(memory, f"task {n}", idle=20 + n)
    first = run(stores, dry_run=True, max_per_run=3, now=NOW)
    assert first == run(stores, dry_run=True, max_per_run=3, now=NOW)
    assert len(first["would_send"]) == 3


def test_new_life_settings_validate(stores):
    _, life = stores
    for bad in ({"suggestion_min_relevance_pct": 101}, {"suggestion_idle_task_days": 0},
                {"suggestions_enabled": "yes"}):
        with pytest.raises(ValueError):
            life.update_settings(bad)
    got = life.update_settings({"suggestion_idle_task_days": 30})
    assert got.suggestion_idle_task_days == 30 and got.suggestions_enabled is True


def test_no_suggestions_when_nothing_is_stalled(stores):
    report = run(stores)
    assert report["evaluated"] == 0 and report["sent"] == [] and report["suppressed"] == []


# ------------------------------------------------------------------ API


def test_api_run_defaults_to_dry_run_and_validates(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    iid = memory.personal.add(kind="task", title="Stalled thing")
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with memory._connection() as db:
        db.execute("UPDATE personal_items SET created_at = ?, updated_at = ? WHERE item_id = ?",
                   (old, old, iid))
    assert client.put("/api/life/settings", json={"outreach_enabled": True,
                                                  "min_outreach_gap_minutes": 0}).status_code == 200
    dry = client.post("/api/life/suggestions/run", json={}).json()
    assert dry["dry_run"] is True and dry["would_send"][0]["evidence"][0]["ref"] == iid
    assert client.get("/api/life/outreach").json()["messages"] == []
    sent = client.post("/api/life/suggestions/run", json={"dry_run": False}).json()
    assert len(sent["sent"]) == 1
    assert len(client.get("/api/life/outreach").json()["messages"]) == 1
    assert client.post("/api/life/suggestions/run", json={"max_per_run": 9}).status_code == 400
    assert client.post("/api/life/suggestions/run", json={"bogus": 1}).status_code == 422
    assert client.put("/api/life/settings", json={"suggestions_enabled": False}).status_code == 200
    off = client.post("/api/life/suggestions/run", json={"dry_run": False}).json()
    assert off["disabled"] == "suggestions_disabled" and len(off["withdrawn"]) == 1
