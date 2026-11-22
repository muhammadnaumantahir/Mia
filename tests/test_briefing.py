"""Daily/weekly briefing from owner-approved sources (MIA-LIFE-05). All fixtures use a fixed clock."""

from datetime import UTC, datetime, timedelta

import pytest

from mia.memory.briefing import generate_briefing
from mia.memory.store import MemoryStore

from .test_brain_pipeline import client_for

# 2026-12-02 01:00 in Karachi (UTC+5, no DST)
NOW = datetime(2026, 12, 1, 20, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory.sqlite3")
    s.settings.update({"owner_timezone": "Asia/Karachi"})
    return s


def at(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def backdate(store, table, key_col, key, days):
    when = (NOW - timedelta(days=days)).isoformat()
    with store._connection() as db:
        db.execute(f"UPDATE {table} SET updated_at = ? WHERE {key_col} = ?", (when, key))  # noqa: S608


def items(brief, key):
    return next(s for s in brief["sections"] if s["key"] == key)["items"]


def titles(brief, key):
    return [e["title"] for e in items(brief, key)]


# ------------------------------------------------------------------ windows and timezone


def test_daily_window_uses_the_owners_local_day(store):
    # Local day 2 Dec ends at 3 Dec 00:00 Karachi = NOW + 23h.
    store.personal.add(kind="task", title="late", due_at=at(-30))
    store.personal.add(kind="task", title="today local", due_at=at(0.5))      # 01:30 local, 2 Dec
    store.personal.add(kind="task", title="last minute", due_at=at(22.9))     # 23:54 local, 2 Dec
    store.personal.add(kind="task", title="tomorrow local", due_at=at(23.5))  # 00:30 local, 3 Dec
    brief = generate_briefing(store, now=NOW)
    assert brief["timezone"] == "Asia/Karachi" and brief["local_time"] == "2026-12-02 01:00"
    assert titles(brief, "overdue") == ["late"]
    assert titles(brief, "upcoming") == ["today local", "last minute"]
    assert items(brief, "upcoming")[0]["due_local"] == "2026-12-02 01:30"
    assert brief["window"]["end"] == "2026-12-02T19:00:00+00:00"


def test_weekly_window_and_horizon(store):
    # 00:30 local on 3 Dec (day3), 8 Dec (day7 of the window), 9 Dec (outside a 7-day window).
    store.personal.add(kind="task", title="3 Dec", due_at=at(23.5))
    store.personal.add(kind="task", title="8 Dec", due_at=at(23.5 + 24 * 5))
    store.personal.add(kind="task", title="9 Dec", due_at=at(23.5 + 24 * 6))
    weekly = generate_briefing(store, period="weekly", now=NOW)
    assert titles(weekly, "upcoming") == ["3 Dec", "8 Dec"]
    assert weekly["window"]["days"] == 7
    assert titles(generate_briefing(store, now=NOW, horizon_days=1), "upcoming") == []
    assert titles(generate_briefing(store, now=NOW, horizon_days=2), "upcoming") == ["3 Dec"]


# ------------------------------------------------------------------ citations and freshness


def test_every_item_cites_a_real_record_and_the_text_shows_it(store):
    t = store.personal.add(kind="task", title="Cited task", due_at=at(1), priority=1, project="Atlas")
    store.projects.create("Atlas", goal="Ship")
    m = store.projects.add_milestone("Atlas", "Cited milestone", due_at=at(2))
    brief = generate_briefing(store, now=NOW)
    sources = {(e["source"]["type"], e["source"]["id"]) for e in items(brief, "upcoming")}
    assert sources == {("personal_item", t), ("project_milestone", m)}
    for kind, rid in sources:
        if kind == "personal_item":
            assert store.personal.get(rid)["title"] == "Cited task"
    assert f"[task {t[:8]}]" in brief["text"] and f"[milestone {m[:8]}]" in brief["text"]
    assert "[high]" in brief["text"] and "(Atlas)" in brief["text"]
    assert brief["freshness"]["personal_items"]["open_count"] == 1
    assert brief["freshness"]["personal_items"]["newest_update"] is not None
    assert brief["generated_at"] == NOW.isoformat()


def test_output_is_deterministic(store):
    store.personal.add(kind="task", title="a", due_at=at(1))
    store.personal.add(kind="task", title="b", due_at=at(1))
    assert generate_briefing(store, now=NOW) == generate_briefing(store, now=NOW)


# ------------------------------------------------------------------ no invention


def test_empty_sources_say_so_and_invent_nothing(store):
    brief = generate_briefing(store, now=NOW)
    assert all(not s["items"] for s in brief["sections"]) and brief["follow_ups"] == []
    sources = [m["source"] for m in brief["missing_sources"]]
    assert sources == ["calendar", "tasks_and_milestones"]
    assert "Nothing is overdue" in brief["text"]
    assert "meeting" not in brief["text"].lower() and "event" in brief["text"].lower()


def test_calendar_is_always_reported_missing_and_never_listed(store):
    store.personal.add(kind="task", title="Dentist appointment", due_at=at(2))
    brief = generate_briefing(store, now=NOW)
    assert brief["missing_sources"][0]["source"] == "calendar"
    assert "No calendar source is connected" in brief["text"]
    assert all(e["source"]["type"] != "calendar" for s in brief["sections"] for e in s["items"])


def test_finished_undated_and_paused_sources(store):
    done = store.personal.add(kind="task", title="finished", due_at=at(-5))
    store.personal.complete(done)
    store.personal.add(kind="task", title="no date")
    store.projects.create("Paused", goal="g", status="paused")
    store.projects.add_milestone("Paused", "hidden milestone", due_at=at(1))
    store.projects.create("Live", goal="g")
    ms = store.projects.add_milestone("Live", "done one", due_at=at(1))
    store.projects.update_milestone("Live", ms, status="done")
    brief = generate_briefing(store, now=NOW)
    assert all(not s["items"] for s in brief["sections"])
    assert brief["undated_open_items"] == 1 and "no due date" in brief["text"]


# ------------------------------------------------------------------ privacy and isolation


def test_private_items_are_hidden_but_counted_unless_included(store):
    store.personal.add(kind="task", title="Visible", due_at=at(1))
    store.personal.add(kind="task", title="Secret errand", due_at=at(1), private=True)
    hidden = generate_briefing(store, now=NOW)
    assert titles(hidden, "upcoming") == ["Visible"]
    assert hidden["hidden_private_count"] == 1
    assert "Secret errand" not in str(hidden) and "1 private item(s) hidden" in hidden["text"]
    shown = generate_briefing(store, now=NOW, include_private=True)
    assert sorted(titles(shown, "upcoming")) == ["Secret errand", "Visible"]
    assert shown["hidden_private_count"] == 0


def test_project_filter_never_leaks_another_project(store):
    for name in ("Atlas", "Borealis"):
        store.projects.create(name, goal=f"{name} goal")
        store.personal.add(kind="task", title=f"{name} task", due_at=at(1), project=name)
        store.projects.add_milestone(name, f"{name} milestone", due_at=at(-3))
    store.personal.add(kind="task", title="loose task", due_at=at(1))
    brief = generate_briefing(store, now=NOW, project="atlas")
    dump = str(brief)
    assert "Atlas task" in dump and "Atlas milestone" in dump
    for foreign in ("Borealis", "loose task"):
        assert foreign not in dump
    with pytest.raises(ValueError):
        generate_briefing(store, now=NOW, project="x" * 65)


# ------------------------------------------------------------------ goal follow-up


def test_goal_follow_up_only_for_stale_active_projects(store):
    store.projects.create("Stale", goal="Finish the book")
    store.projects.create("Fresh", goal="Ship app")
    store.projects.create("NoGoal")
    store.projects.create("Quiet", goal="Old but paused", status="paused")
    for name in ("Stale", "NoGoal", "Quiet"):
        backdate(store, "projects", "project_key", name.casefold(), 20)
    backdate(store, "projects", "project_key", "fresh", 20)
    store.personal.add(kind="task", title="recent work", project="Fresh")  # recent activity
    brief = generate_briefing(store, now=NOW, stale_goal_days=14)
    assert [f["project"] for f in brief["follow_ups"]] == ["Stale"]
    follow = brief["follow_ups"][0]
    assert follow["source"] == {"type": "project", "id": "stale"}
    assert "20 days" in follow["reason"] and "Finish the book" in brief["text"]
    assert generate_briefing(store, now=NOW, stale_goal_days=30)["follow_ups"] == []


# ------------------------------------------------------------------ quiet hours and limits


@pytest.mark.parametrize(("start", "end", "active"), [
    (60, 120, True),     # 01:00-02:00 local, now 01:00
    (120, 300, False),   # 02:00-05:00
    (1380, 360, True),   # 23:00-06:00 overnight window contains 01:00
    (360, 1380, False),  # 06:00-23:00
    (60, 60, False),     # empty window
])
def test_quiet_hours_gate_delivery_not_content(store, start, end, active):
    store.personal.add(kind="task", title="x", due_at=at(1))
    brief = generate_briefing(store, now=NOW, quiet_start_minute=start, quiet_end_minute=end)
    assert brief["quiet_hours_active"] is active and brief["deliver_now"] is (not active)
    assert titles(brief, "upcoming") == ["x"]  # content is still built
    assert ("Quiet hours are active" in brief["text"]) is active


def test_default_has_no_quiet_hours(store):
    brief = generate_briefing(store, now=NOW)
    assert brief["quiet_hours_active"] is False and brief["deliver_now"] is True


def test_limit_reports_omitted_items(store):
    for n in range(5):
        store.personal.add(kind="task", title=f"t{n}", due_at=at(1 + n / 10))
    brief = generate_briefing(store, now=NOW, max_items=2)
    assert titles(brief, "upcoming") == ["t0", "t1"]
    assert brief["omitted_by_limit"] == 3 and "3 more not shown" in brief["text"]


def test_validation(store):
    for kw in ({"period": "monthly"}, {"horizon_days": 0}, {"horizon_days": 32},
               {"stale_goal_days": 0}, {"max_items": 0}, {"max_items": 51},
               {"quiet_start_minute": 60}, {"quiet_start_minute": 1440, "quiet_end_minute": 0},
               {"quiet_start_minute": True, "quiet_end_minute": 0}):
        with pytest.raises(ValueError):
            generate_briefing(store, now=NOW, **kw)


def test_unknown_timezone_setting_falls_back_to_utc_and_says_so(store):
    with store._connection() as db:
        db.execute("INSERT OR REPLACE INTO memory_settings (key, value_json, updated_at) "
                   "VALUES ('owner_timezone', '\"Mars/Olympus\"', 'x')")
    assert generate_briefing(store, now=NOW)["timezone"] == "UTC"


# ------------------------------------------------------------------ API


def test_api_briefing(tmp_path, monkeypatch):
    client, memory, _ = client_for(tmp_path, monkeypatch)
    soon = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    memory.personal.add(kind="task", title="API overdue", due_at=past)
    memory.personal.add(kind="task", title="API private", due_at=past, private=True)
    body = client.get("/api/briefing").json()
    assert body["period"] == "daily" and body["timezone"] == "UTC"
    assert [e["title"] for e in items(body, "overdue")] == ["API overdue"]
    assert body["hidden_private_count"] == 1 and body["deliver_now"] is True
    assert soon  # clock-independent assertions only
    assert client.get("/api/briefing", params={"period": "weekly", "include_private": True}
                      ).json()["include_private"] is True
    assert client.get("/api/briefing", params={"period": "yearly"}).status_code == 400
    assert client.get("/api/briefing", params={"quiet_start_minute": 5}).status_code == 400


def test_item_due_exactly_now_is_upcoming_not_overdue(store):
    store.personal.add(kind="task", title="due right now", due_at=NOW.isoformat())
    brief = generate_briefing(store, now=NOW)
    assert titles(brief, "overdue") == [] and titles(brief, "upcoming") == ["due right now"]
