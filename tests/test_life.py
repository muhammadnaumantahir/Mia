import json
import math
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.life.runner import (
    NO_MODEL_RESOURCE,
    ExplorationResult,
    LifeRunner,
    LifeScheduler,
    MemoryReviewExplorer,
    derive_followups,
    score_item,
)
from mia.life.store import LifeSettings, LifeStore, validate_settings
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def ok_resources():
    return {"cpu_percent": 10.0, "memory_percent": 20.0}


def make_parts(tmp_path, *, explorer=None, resources=ok_resources, **settings):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    life = LifeStore(tmp_path / "life.sqlite3")
    life.update_settings({"min_interval_seconds": 0, **settings})
    runner = LifeRunner(life, memory, explorer, resource_snapshot=resources)
    return memory, life, runner


def add_item(life, title="Falcon runtime scheduler quiet hours", **kw):
    kw.setdefault("why_it_matters", "Understand how the Falcon scheduler handles quiet hours")
    kw.setdefault("origin", "owner")
    return life.add_item(title=title, **kw)[0]


def seed_falcon_memory(memory):
    memory.add_item(
        kind="fact",
        title="Falcon runtime scheduler",
        content="Falcon runtime scheduler notes about quiet hours",
        trust_level="user",
    )


def age_goal(memory, record_id, days):
    old = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    db = sqlite3.connect(memory.database_path)
    try:
        db.execute("UPDATE structured_memory SET updated_at = ? WHERE record_id = ?", (old, record_id))
        db.commit()
    finally:
        db.close()


class ScriptedExplorer:
    name = "scripted"
    permission_level = 0

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0

    def explore(self, item, context):
        self.calls += 1
        return self.behaviour(item, context)


# ------------------------------------------------------------------ settings


def test_default_settings_are_conservative(tmp_path):
    settings = LifeStore(tmp_path / "life.sqlite3").get_settings()
    assert settings == LifeSettings()
    assert settings.paused is False and settings.emergency_stop is False
    assert settings.max_cycles_per_day == 24 and settings.time_budget_seconds == 20


@pytest.mark.parametrize(
    "changes",
    [
        {"nonsense": 1},
        {"paused": "yes"},
        {"max_cycles_per_day": 0},
        {"max_cycles_per_day": 501},
        {"max_cycles_per_day": True},
        {"max_cycles_per_day": 2.5},
        {"max_cycles_per_day": math.nan},
        {"max_cycles_per_day": math.inf},
        {"max_cycles_per_day": "10"},
        {"time_budget_seconds": 0},
        {"time_budget_seconds": 601},
        {"max_cpu_percent": 5},
        {"max_cpu_percent": 101},
        {"min_interval_seconds": -1},
    ],
)
def test_invalid_settings_are_rejected_and_nothing_changes(tmp_path, changes):
    life = LifeStore(tmp_path / "life.sqlite3")
    with pytest.raises(ValueError):
        life.update_settings(changes)
    assert life.get_settings() == LifeSettings()


def test_settings_persist_across_instances_and_merge(tmp_path):
    first = LifeStore(tmp_path / "life.sqlite3")
    first.update_settings({"max_cycles_per_day": 5})
    first.update_settings({"paused": True, "time_budget_seconds": 7.0})
    reopened = LifeStore(tmp_path / "life.sqlite3").get_settings()
    assert reopened.max_cycles_per_day == 5 and reopened.paused is True
    assert reopened.time_budget_seconds == 7 and isinstance(reopened.time_budget_seconds, int)


def test_corrupted_settings_fail_safe_to_paused(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    db = sqlite3.connect(life.database_path)
    db.execute(
        "INSERT INTO life_settings (key, value_json, updated_at) VALUES ('settings', ?, 'x')",
        (json.dumps({"max_cycles_per_day": 99999}),),
    )
    db.commit()
    db.close()
    assert life.get_settings().paused is True


def test_validate_settings_accepts_whole_floats():
    assert validate_settings({"stale_goal_days": 14.0}) == {"stale_goal_days": 14}


# ------------------------------------------------------------------- backlog


@pytest.mark.parametrize(
    "kwargs",
    [
        {"origin": "robot"},
        {"title": "  "},
        {"why_it_matters": ""},
        {"title": "x" * 301},
        {"why_it_matters": "x" * 4001},
        {"expected_value": 1.5},
        {"expected_value": -0.1},
        {"novelty": math.nan},
        {"novelty": math.inf},
        {"expected_value": True},
    ],
)
def test_invalid_curiosity_items_are_rejected(tmp_path, kwargs):
    life = LifeStore(tmp_path / "life.sqlite3")
    base = {"title": "T", "why_it_matters": "W", "origin": "owner"}
    base.update(kwargs)
    with pytest.raises(ValueError):
        life.add_item(**base)
    assert life.list_items() == []


def test_duplicate_open_titles_and_source_keys_are_not_added_twice(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    first, created = life.add_item(title="Learn Rust!", why_it_matters="w", origin="owner")
    again, created_again = life.add_item(title="learn   rust", why_it_matters="w", origin="owner")
    assert created and not created_again and again == first
    keyed, _ = life.add_item(title="Other", why_it_matters="w", origin="self", source_key="k1")
    same_key, made = life.add_item(title="Renamed", why_it_matters="w", origin="self", source_key="k1")
    assert same_key == keyed and not made
    life.set_item_state(first, "done")
    reopened, created_after_done = life.add_item(title="Learn Rust", why_it_matters="w", origin="owner")
    assert created_after_done and reopened != first  # a finished topic may be raised again


def test_eligibility_respects_attempt_cap_and_deferral(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    fresh = add_item(life, "Fresh topic about gardens")
    capped = add_item(life, "Capped topic about tides")
    for _ in range(3):
        life.set_item_state(capped, "queued", count_attempt=True)
    waiting = add_item(life, "Deferred topic about comets")
    later = datetime.now(UTC) + timedelta(hours=5)
    life.set_item_state(waiting, "deferred", defer_until=later)
    blocked = add_item(life, "Blocked topic about moss")
    life.set_item_state(blocked, "blocked")

    now = datetime.now(UTC)
    assert {i["item_id"] for i in life.eligible_items(max_attempts=3, now=now)} == {fresh}
    soon = {i["item_id"] for i in life.eligible_items(max_attempts=3, now=later + timedelta(minutes=1))}
    assert soon == {fresh, waiting}


def test_set_state_unknown_item_and_state(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    item = add_item(life)
    with pytest.raises(KeyError):
        life.set_item_state("missing", "done")
    with pytest.raises(ValueError):
        life.set_item_state(item, "flying")
    life.set_item_state(item, "queued", count_attempt=True)
    life.set_item_state(item, "queued", reset_attempts=True)
    assert life.get_item(item)["attempts"] == 0


# ------------------------------------------------------------------- journal


def test_journal_requires_valid_trigger_and_outcome_and_finishes_once(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    with pytest.raises(ValueError):
        life.start_cycle(trigger="whim", trigger_detail="", goal="g", rationale="r", item_id=None, policy={})
    cycle = life.start_cycle(trigger="owner", trigger_detail="d", goal="g", rationale="r", item_id=None, policy={})
    assert life.get_cycle(cycle)["status"] == "running"
    with pytest.raises(ValueError):
        life.finish_cycle(cycle, outcome="great-success", summary="s")
    life.finish_cycle(cycle, outcome="completed", summary="s", duration_ms=3)
    entry = life.get_cycle(cycle)
    assert entry["status"] == "finished" and entry["lease_owner"] is None
    with pytest.raises(KeyError):
        life.finish_cycle(cycle, outcome="failed", summary="again")
    assert entry["notification_decision"].startswith("none")


def test_no_op_cycles_are_journaled_and_identical_repeats_coalesce(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    first = life.record_no_op(trigger="schedule", trigger_detail="t", reason="nothing", policy={})
    second = life.record_no_op(trigger="schedule", trigger_detail="t", reason="nothing", policy={})
    assert first == second
    entries = life.list_journal()
    assert len(entries) == 1 and entries[0]["repeat_count"] == 2 and entries[0]["outcome"] == "no_op"
    assert life.cycles_started_since(datetime.now(UTC) - timedelta(hours=1)) == 2  # still counted

    other = life.start_cycle(trigger="owner", trigger_detail="d", goal="g", rationale="r", item_id=None, policy={})
    life.finish_cycle(other, outcome="completed", summary="did something")
    third = life.record_no_op(trigger="schedule", trigger_detail="t", reason="nothing", policy={})
    assert third not in (first, other)
    assert len(life.list_journal()) == 3


def test_recovery_fails_other_instances_cycles_and_requeues_without_replay(tmp_path):
    dead = LifeStore(tmp_path / "life.sqlite3")
    item = add_item(dead)
    cycle = dead.start_cycle(trigger="schedule", trigger_detail="t", goal="g", rationale="r", item_id=item, policy={})
    dead.set_item_state(item, "in_progress")

    mine = LifeStore(tmp_path / "life.sqlite3")  # a new process: different instance id
    assert mine.instance_id != dead.instance_id
    assert mine.recover_interrupted() == []  # lease still valid and held elsewhere
    assert mine.get_cycle(cycle)["status"] == "running"

    assert mine.recover_interrupted(at_startup=True) == [cycle]
    entry = mine.get_cycle(cycle)
    assert entry["status"] == "finished" and entry["outcome"] == "failed" and entry["error"] == "interrupted"
    assert "nothing was resumed" in entry["summary"]
    recovered = mine.get_item(item)
    assert recovered["state"] == "queued" and recovered["attempts"] == 1


def test_recovery_never_touches_its_own_running_cycle(tmp_path):
    life = LifeStore(tmp_path / "life.sqlite3")
    cycle = life.start_cycle(trigger="owner", trigger_detail="d", goal="g", rationale="r", item_id=None, policy={})
    assert life.recover_interrupted() == [] and life.recover_interrupted(at_startup=True) == []
    assert life.get_cycle(cycle)["status"] == "running"


def test_expired_lease_of_another_instance_is_recovered_without_startup(tmp_path):
    dead = LifeStore(tmp_path / "life.sqlite3")
    cycle = dead.start_cycle(trigger="schedule", trigger_detail="t", goal="g", rationale="r", item_id=None, policy={})
    db = sqlite3.connect(dead.database_path)
    db.execute(
        "UPDATE activity_journal SET lease_expires_at = ? WHERE cycle_id = ?",
        ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), cycle),
    )
    db.commit()
    db.close()
    assert LifeStore(tmp_path / "life.sqlite3").recover_interrupted() == [cycle]


# ------------------------------------------------------- follow-up derivation


def test_stale_goals_create_one_followup_and_fresh_goals_none(tmp_path):
    memory, life, _ = make_parts(tmp_path)
    stale, _ = memory.structured.add(record_type="goal", title="Ship version one", body="Release v1")
    fresh, _ = memory.structured.add(record_type="goal", title="Learn guitar", body="Practise weekly")
    memory.structured.add(record_type="decision", title="Old decision", body="Use SQLite")
    age_goal(memory, stale, 30)
    settings = life.get_settings()
    now = datetime.now(UTC)

    assert derive_followups(life, memory, settings, now) == 1
    assert derive_followups(life, memory, settings, now) == 0  # idempotent
    items = life.list_items()
    assert len(items) == 1 and items[0]["origin"] == "goal_followup"
    assert items[0]["title"] == "Review progress on goal: Ship version one"
    assert items[0]["source_ref"] == {"type": "record", "ref": stale}
    assert "30 days" in items[0]["why_it_matters"]
    assert fresh not in json.dumps(items)


def test_completed_goal_does_not_generate_followups(tmp_path):
    memory, life, _ = make_parts(tmp_path)
    goal, _ = memory.structured.add(record_type="goal", title="Ship v1", body="Release")
    memory.structured.set_status(goal, "completed")
    age_goal(memory, goal, 90)
    assert derive_followups(life, memory, life.get_settings(), datetime.now(UTC)) == 0


def test_scoring_prefers_value_and_penalises_repeated_attempts():
    now = datetime.now(UTC)
    base = {"novelty": 0.5, "created_at": now.isoformat(), "attempts": 0, "expected_value": 0.9}
    high, why = score_item(base, now)
    low, _ = score_item({**base, "expected_value": 0.2}, now)
    tried, _ = score_item({**base, "attempts": 2}, now)
    old, _ = score_item({**base, "created_at": (now - timedelta(days=30)).isoformat()}, now)
    assert high > low and high > tried and old > high
    assert "value 0.90" in why and "attempts 0" in why


# ---------------------------------------------------------------- gating


def test_skipped_cycles_are_never_journaled(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    add_item(life)
    life.update_settings({"paused": True})
    assert runner.run_cycle("schedule") == {"status": "skipped", "reason": "paused"}
    assert life.list_journal() == [] and life.get_item(life.list_items()[0]["item_id"])["state"] == "queued"


def test_emergency_stop_blocks_even_owner_requests_but_pause_does_not(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    seed_falcon_memory(memory)
    add_item(life)
    life.update_settings({"paused": True})
    assert runner.run_cycle("owner")["status"] == "completed"  # explicit owner request
    life.update_settings({"emergency_stop": True})
    assert runner.run_cycle("owner") == {"status": "skipped", "reason": "emergency_stop"}
    assert len(life.list_journal()) == 1


def test_daily_cycle_cap_and_min_interval(tmp_path):
    memory, life, runner = make_parts(tmp_path, max_cycles_per_day=1)
    assert runner.run_cycle("schedule")["status"] == "no_op"
    assert runner.run_cycle("schedule") == {"status": "skipped", "reason": "daily_cycle_cap"}
    assert runner.run_cycle("owner")["status"] == "no_op"  # owner request ignores the cap

    memory2, life2, runner2 = make_parts(tmp_path / "second", min_interval_seconds=3600)
    assert runner2.run_cycle("schedule")["status"] == "no_op"
    assert runner2.run_cycle("schedule") == {"status": "skipped", "reason": "min_interval"}


@pytest.mark.parametrize(
    ("resources", "reason"),
    [
        (None, "resources_unavailable"),
        (lambda: {"cpu_percent": 95.0, "memory_percent": 20.0}, "resources_busy"),
        (lambda: {"cpu_percent": 10.0, "memory_percent": 99.0}, "resources_busy"),
        (lambda: (_ for _ in ()).throw(RuntimeError("psutil missing")), "resources_unavailable"),
        (lambda: {"cpu_percent": math.nan, "memory_percent": 10.0}, "resources_unavailable"),
        (lambda: {"cpu_percent": -5.0, "memory_percent": 10.0}, "resources_unavailable"),
        (lambda: {"memory_percent": 10.0}, "resources_unavailable"),
    ],
)
def test_background_cycles_fail_closed_on_missing_bad_or_busy_resources(tmp_path, resources, reason):
    memory, life, runner = make_parts(tmp_path, resources=resources)
    add_item(life)
    assert runner.run_cycle("schedule") == {"status": "skipped", "reason": reason}
    assert life.list_journal() == []


def test_overlapping_cycles_are_refused(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(item, context):
        started.set()
        release.wait(5)
        return ExplorationResult(outcome="completed", item_state="done", summary="slow")

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(slow), time_budget_seconds=10)
    add_item(life)
    worker = threading.Thread(target=runner.run_cycle, args=("owner",))
    worker.start()
    assert started.wait(5)
    assert runner.run_cycle("owner") == {"status": "skipped", "reason": "cycle_already_running"}
    release.set()
    worker.join(5)
    assert len(life.list_journal()) == 1


# --------------------------------------------------------- real exploration


def test_memory_review_cycle_records_complete_honest_evidence(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    seed_falcon_memory(memory)
    item = add_item(life)

    result = runner.run_cycle("schedule", "interval elapsed")
    assert result["status"] == "completed" and result["item_id"] == item

    entry = life.get_cycle(result["cycle_id"])
    assert entry["status"] == "finished" and entry["outcome"] == "completed"
    assert entry["trigger"] == "schedule" and entry["trigger_detail"] == "interval elapsed"
    assert entry["goal"].startswith("Investigate: Falcon runtime")
    assert "score " in entry["rationale"] and "1 eligible" in entry["rationale"]
    assert entry["permission_level"] == 0
    assert entry["policy"]["gates_passed"] is True and entry["policy"]["time_budget_seconds"] == 20
    assert entry["resource"] == NO_MODEL_RESOURCE  # no model, no calls, no cost
    assert entry["actions"] == [
        "memory.search:knowledge", "memory.search:records", "memory.search:messages",
    ]
    assert [s["type"] for s in entry["sources"]] == ["knowledge"]
    assert "No external research was performed" in entry["summary"]
    assert entry["duration_ms"] >= 0 and entry["error"] is None
    assert entry["notification_decision"].startswith("none")

    stored = life.get_item(item)
    assert stored["state"] == "done" and stored["attempts"] == 0
    assert stored["result"]["external_research"] is False
    assert runner.last_result["status"] == "completed"


def test_explorer_never_writes_memory(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    seed_falcon_memory(memory)
    add_item(life)
    before = memory.export_data()
    runner.run_cycle("owner")
    after = memory.export_data()
    assert before["memory_items"] == after["memory_items"]
    assert before["structured_memory"] == after["structured_memory"]
    assert before["conversations"] == after["conversations"]


def test_nothing_found_is_reported_as_deferred_and_item_is_blocked_not_invented(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    item = add_item(life, "Zebra crossing economics", why_it_matters="Curious about zebra crossings")
    result = runner.run_cycle("owner")
    assert result["status"] == "deferred"
    entry = life.get_cycle(result["cycle_id"])
    assert entry["outcome"] == "deferred" and entry["sources"] == []
    assert "no external research source is enabled" in entry["summary"]
    stored = life.get_item(item)
    assert stored["state"] == "blocked" and stored["state_reason"] == "no_enabled_research_source"
    assert stored["attempts"] == 1
    assert life.eligible_items(max_attempts=3) == []  # does not spin on the same item


def test_goal_followup_does_not_find_the_goal_it_came_from(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    goal, _ = memory.structured.add(record_type="goal", title="Ship version one", body="Release version one")
    age_goal(memory, goal, 30)
    result = runner.run_cycle("owner")
    assert result["status"] == "deferred"
    entry = life.get_cycle(result["cycle_id"])
    assert entry["sources"] == []
    assert entry["goal"] == "Investigate: Review progress on goal: Ship version one"


def test_followup_finds_related_decision_but_not_itself(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    goal, _ = memory.structured.add(record_type="goal", title="Ship version one", body="Release version one")
    decision, _ = memory.structured.add(
        record_type="decision", title="Version one release process", body="Ship version one on Fridays"
    )
    age_goal(memory, goal, 30)
    entry = life.get_cycle(runner.run_cycle("owner")["cycle_id"])
    assert entry["outcome"] == "completed"
    assert [(s["type"], s["ref"]) for s in entry["sources"]] == [("record", decision)]


def test_highest_scoring_item_runs_first_and_cycle_explains_why(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    seed_falcon_memory(memory)
    add_item(life, "Low priority gardening trivia", why_it_matters="Idle curiosity", expected_value=0.1)
    best = add_item(life, expected_value=0.95)
    result = runner.run_cycle("owner")
    assert result["item_id"] == best
    assert "2 eligible" in life.get_cycle(result["cycle_id"])["rationale"]


def test_empty_backlog_records_an_honest_no_op(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    result = runner.run_cycle("schedule")
    entry = life.get_cycle(result["cycle_id"])
    assert result["status"] == "no_op" and entry["outcome"] == "no_op"
    assert entry["sources"] == [] and entry["actions"] == []
    assert "nothing to investigate" in entry["summary"]


# ---------------------------------------------------- failure and interruption


def test_explorer_exception_fails_the_cycle_and_defers_the_item(tmp_path):
    def boom(item, context):
        raise RuntimeError("explorer exploded with detail that must not leak")

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(boom))
    item = add_item(life)
    result = runner.run_cycle("owner")
    assert result == {"status": "failed", "cycle_id": result["cycle_id"], "item_id": item, "reason": "RuntimeError"}
    entry = life.get_cycle(result["cycle_id"])
    assert entry["outcome"] == "failed" and entry["error"] == "RuntimeError"
    assert "leak" not in json.dumps(entry)
    stored = life.get_item(item)
    assert stored["state"] == "deferred" and stored["attempts"] == 1 and stored["defer_until"]
    assert life.eligible_items(max_attempts=3) == []  # waits an hour before retry


def test_time_budget_is_enforced_and_recorded(tmp_path):
    release = threading.Event()

    def hang(item, context):
        release.wait(10)
        return ExplorationResult(outcome="completed", item_state="done", summary="late")

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(hang), time_budget_seconds=1)
    item = add_item(life)
    started = time.monotonic()
    try:
        result = runner.run_cycle("owner")
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert result["status"] == "failed" and result["reason"] == "time_budget_exceeded"
    entry = life.get_cycle(result["cycle_id"])
    assert entry["error"] == "time_budget_exceeded" and entry["outcome"] == "failed"
    assert life.get_item(item)["state"] == "deferred"


def test_emergency_stop_during_a_cycle_discards_findings_and_requeues(tmp_path):
    def stop_midway(item, context):
        life.update_settings({"emergency_stop": True})
        assert context.should_stop() is True
        return ExplorationResult(
            outcome="completed", item_state="done", summary="findings that must not be recorded",
            sources=[{"type": "knowledge", "ref": "x"}], actions=["memory.search:knowledge"],
        )

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(stop_midway))
    item = add_item(life)
    result = runner.run_cycle("owner")
    assert result["status"] == "cancelled"
    entry = life.get_cycle(result["cycle_id"])
    assert entry["outcome"] == "cancelled" and entry["sources"] == []
    assert "findings" not in entry["summary"]
    stored = life.get_item(item)
    assert stored["state"] == "queued" and stored["attempts"] == 0 and stored["result"] is None


def test_pause_during_a_background_cycle_cancels_it_but_not_an_owner_cycle(tmp_path):
    def pause_midway(item, context):
        life.update_settings({"paused": True})
        return ExplorationResult(outcome="completed", item_state="done", summary="finished")

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(pause_midway))
    add_item(life, "First topic about harbours")
    assert runner.run_cycle("schedule")["status"] == "cancelled"
    life.update_settings({"paused": False})
    assert runner.run_cycle("owner")["status"] == "completed"


def test_explorer_receives_a_deadline_and_stop_hook(tmp_path):
    seen = {}

    def inspect(item, context):
        seen["remaining"] = context.deadline - time.monotonic()
        seen["stop"] = context.should_stop()
        return ExplorationResult(outcome="completed", item_state="done", summary="ok")

    memory, life, runner = make_parts(tmp_path, explorer=ScriptedExplorer(inspect), time_budget_seconds=7)
    add_item(life)
    runner.run_cycle("owner")
    assert 0 < seen["remaining"] <= 7 and seen["stop"] is False


def test_on_cycle_hook_fires_only_for_real_cycles_and_cannot_break_scheduling(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    events = []

    def hook(result):
        events.append(result)
        raise RuntimeError("observer failed")

    runner.on_cycle = hook
    assert runner.run_cycle("schedule")["status"] == "no_op"
    life.update_settings({"paused": True})
    runner.run_cycle("schedule")  # skipped: no event
    assert [e["status"] for e in events] == ["no_op"] and events[0]["trigger"] == "schedule"


def test_status_reports_what_is_and_is_not_enabled(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    add_item(life)
    status = runner.status()
    assert status["curiosity_counts"] == {"queued": 1} and status["cycles_today"] == 0
    assert status["external_research_enabled"] is False
    assert status["proactive_delivery_enabled"] is False
    assert status["explorer"] == {"name": "memory_review", "permission_level": 0}
    assert isinstance(runner.explorer, MemoryReviewExplorer)


# ----------------------------------------------------------------- scheduler


def test_scheduler_validates_interval():
    for bad in (0.0, 4.9, 86_401.0):
        with pytest.raises(ValueError):
            LifeScheduler(object(), interval_seconds=bad)  # type: ignore[arg-type]


def test_scheduler_runs_event_triggered_cycles_and_stops_cleanly(tmp_path):
    memory, life, runner = make_parts(tmp_path)
    seed_falcon_memory(memory)
    scheduler = LifeScheduler(runner, interval_seconds=3600.0)
    scheduler.start()
    scheduler.start()  # idempotent
    try:
        assert scheduler.running
        add_item(life)
        scheduler.wake("owner added a curiosity item")
        deadline = time.monotonic() + 5
        while not life.list_journal() and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        assert scheduler.stop(5.0) is True
    entries = life.list_journal()
    assert len(entries) == 1 and entries[0]["trigger"] == "event"
    assert entries[0]["trigger_detail"] == "owner added a curiosity item"
    assert not scheduler.running


def test_scheduler_loop_survives_runner_exceptions(tmp_path):
    memory, life, runner = make_parts(tmp_path)

    def broken(trigger, detail):
        raise RuntimeError("unexpected")

    runner.run_cycle = broken  # type: ignore[method-assign]
    scheduler = LifeScheduler(runner, interval_seconds=3600.0)
    scheduler.start()
    try:
        scheduler.wake("one")
        deadline = time.monotonic() + 5
        while runner.last_result is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert runner.last_result == {"status": "error", "reason": "unexpected_runtime_error"}
        assert scheduler.running
    finally:
        scheduler.stop(5.0)


# ----------------------------------------------------------------------- API


class FakeMonitor:
    def snapshot(self):
        return ok_resources()


def make_client(tmp_path, monkeypatch, *, life=None, background=False):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_LIFE_ENABLED", "true" if background else "false")
    observatory = ObservatoryStore(tmp_path / "observatory.sqlite3")
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    life = life or LifeStore(tmp_path / "life.sqlite3")
    app = create_app(
        store=observatory,
        gateway=ModelGateway(ProviderRegistry()),
        memory=memory,
        life=life,
        resource_monitor=FakeMonitor(),
    )
    return TestClient(app), memory, life, observatory


def event_types(observatory):
    return [e["event_type"] for e in observatory.list_events(200)]


def test_api_owner_controls_and_audit(tmp_path, monkeypatch):
    client, _, life, observatory = make_client(tmp_path, monkeypatch)
    status = client.get("/api/life/status").json()
    assert status["background_enabled"] is False and status["scheduler_running"] is False
    assert status["settings"]["paused"] is False

    updated = client.put("/api/life/settings", json={"max_cycles_per_day": 3, "time_budget_seconds": 9})
    assert updated.status_code == 200 and updated.json()["settings"]["max_cycles_per_day"] == 3
    assert life.get_settings().time_budget_seconds == 9
    assert client.put("/api/life/settings", json={}).status_code == 400
    assert client.put("/api/life/settings", json={"max_cycles_per_day": 0}).status_code == 400
    assert client.put("/api/life/settings", json={"paused": True}).status_code == 422  # not owner-settable here
    assert client.put("/api/life/settings", json={"bogus": 1}).status_code == 422

    assert client.post("/api/life/pause").json() == {"paused": True}
    assert life.get_settings().paused is True

    stop = client.post("/api/life/emergency-stop").json()
    assert stop == {"emergency_stop": True, "paused": True}
    assert client.post("/api/life/resume").status_code == 409
    assert client.post("/api/life/cycle").json() == {"status": "skipped", "reason": "emergency_stop"}

    cleared = client.post("/api/life/emergency-stop/clear").json()
    assert cleared == {"emergency_stop": False, "paused": True}  # stays paused until resumed
    assert client.post("/api/life/resume").json()["paused"] is False

    types = event_types(observatory)
    for expected in ("settings_changed", "paused", "emergency_stop", "emergency_stop_cleared", "resumed"):
        assert expected in types


def test_api_curiosity_backlog_lifecycle(tmp_path, monkeypatch):
    client, _, life, observatory = make_client(tmp_path, monkeypatch)
    created = client.post("/api/life/curiosity", json={"title": "Learn about tides", "why_it_matters": "Curious"})
    assert created.status_code == 201 and created.json()["created"] is True
    item_id = created.json()["item_id"]
    again = client.post("/api/life/curiosity", json={"title": "learn about tides", "why_it_matters": "x"})
    assert again.json() == {"item_id": item_id, "created": False}

    assert client.post("/api/life/curiosity", json={"title": "T", "why_it_matters": "W", "novelty": 2}).status_code == 400
    assert client.post("/api/life/curiosity", json={"title": "", "why_it_matters": "W"}).status_code == 422

    listed = client.get("/api/life/curiosity").json()["items"]
    assert [i["item_id"] for i in listed] == [item_id] and listed[0]["origin"] == "owner"
    assert client.get("/api/life/curiosity", params={"state": "queued"}).status_code == 200
    assert client.get("/api/life/curiosity", params={"state": "bogus"}).status_code == 400

    assert client.post(f"/api/life/curiosity/{item_id}/state", json={"state": "done"}).status_code == 400
    assert client.post("/api/life/curiosity/missing/state", json={"state": "dropped"}).status_code == 404
    assert client.post(f"/api/life/curiosity/{item_id}/state", json={"state": "rejected"}).status_code == 200
    assert life.get_item(item_id)["state"] == "rejected"
    assert client.post(f"/api/life/curiosity/{item_id}/state", json={"state": "queued"}).status_code == 200
    assert life.get_item(item_id)["attempts"] == 0

    assert client.delete(f"/api/life/curiosity/{item_id}").json() == {"deleted": True}
    assert client.delete(f"/api/life/curiosity/{item_id}").json() == {"deleted": False}
    types = event_types(observatory)
    for expected in ("curiosity_added", "curiosity_state_changed", "curiosity_deleted"):
        assert expected in types


def test_api_owner_cycle_produces_a_readable_journal_and_ids_only_events(tmp_path, monkeypatch):
    client, memory, life, observatory = make_client(tmp_path, monkeypatch)
    seed_falcon_memory(memory)
    item_id = client.post("/api/life/curiosity", json={
        "title": "Falcon runtime scheduler quiet hours",
        "why_it_matters": "SecretWhyXYZ understand the Falcon scheduler quiet hours",
    }).json()["item_id"]

    result = client.post("/api/life/cycle").json()
    assert result["status"] == "completed" and result["item_id"] == item_id

    journal = client.get("/api/life/journal").json()["entries"]
    assert [e["cycle_id"] for e in journal] == [result["cycle_id"]]
    detail = client.get(f"/api/life/journal/{result['cycle_id']}").json()
    assert detail["trigger"] == "owner" and detail["sources"] and detail["resource"]["model_calls"] == 0
    assert client.get("/api/life/journal/missing").status_code == 404
    assert client.get("/api/life/journal", params={"outcome": "failed"}).json()["entries"] == []

    status = client.get("/api/life/status").json()
    assert status["cycles_today"] == 1 and status["last_result"]["status"] == "completed"
    assert status["curiosity_counts"] == {"done": 1}

    cycle_events = [e for e in observatory.list_events(200) if e["event_type"] == "cycle_completed"]
    assert len(cycle_events) == 1
    dumped = json.dumps(observatory.list_events(200))
    assert "SecretWhyXYZ" not in dumped  # events carry ids, never content


def test_api_startup_recovers_cycles_left_by_a_crashed_process(tmp_path, monkeypatch):
    crashed = LifeStore(tmp_path / "life.sqlite3")
    item = add_item(crashed)
    cycle = crashed.start_cycle(trigger="schedule", trigger_detail="t", goal="g", rationale="r", item_id=item, policy={})
    crashed.set_item_state(item, "in_progress")

    client, _, life, observatory = make_client(tmp_path, monkeypatch, life=LifeStore(tmp_path / "life.sqlite3"))
    with client:  # runs the lifespan startup
        entry = client.get(f"/api/life/journal/{cycle}").json()
        assert entry["outcome"] == "failed" and entry["error"] == "interrupted"
        assert life.get_item(item)["state"] == "queued" and life.get_item(item)["attempts"] == 1
    assert "cycles_recovered" in event_types(observatory)


def test_background_scheduler_is_off_by_default_and_respects_pause_at_startup(tmp_path, monkeypatch):
    client, _, life, _ = make_client(tmp_path, monkeypatch, background=False)
    with client:
        assert client.get("/api/life/status").json()["scheduler_running"] is False
    assert not client.app.state.life_scheduler.running

    paused_client, _, paused_life, _ = make_client(tmp_path / "p", monkeypatch, background=True)
    paused_life.update_settings({"paused": True})
    with paused_client:
        assert paused_client.get("/api/life/status").json()["scheduler_running"] is False

    live_client, _, _, _ = make_client(tmp_path / "l", monkeypatch, background=True)
    with live_client:
        assert live_client.get("/api/life/status").json()["scheduler_running"] is True
    assert not live_client.app.state.life_scheduler.running  # stopped on shutdown


def test_event_wake_runs_a_cycle_when_the_owner_adds_an_item(tmp_path, monkeypatch):
    client, memory, life, _ = make_client(tmp_path, monkeypatch, background=True)
    life.update_settings({"min_interval_seconds": 0})
    seed_falcon_memory(memory)
    with client:
        client.post("/api/life/curiosity", json={
            "title": "Falcon runtime scheduler quiet hours", "why_it_matters": "Understand Falcon scheduler quiet hours",
        })
        deadline = time.monotonic() + 5
        while not life.list_journal() and time.monotonic() < deadline:
            time.sleep(0.02)
    entries = life.list_journal()
    assert entries and entries[-1]["trigger"] == "event"
