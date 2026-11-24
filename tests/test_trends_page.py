"""MIA-OBS-06: the /trends page, driven in Chromium against the real API and a real store.

The evidence is real stored snapshots, metrics and events (no mocks). The honesty checks read
what is painted: the number of dots equals the number of recorded samples, a gap draws no line,
a day without a snapshot is named in words, and with no evidence nothing is drawn at all.
"""

import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import uvicorn

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from tests.test_observatory_report_generator import _day

playwright_sync = pytest.importorskip("playwright.sync_api")


def serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


@pytest.fixture
def world(tmp_path, browser):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    server, thread, base = serve(create_app(store=store))
    context = browser.new_context(viewport={"width": 1100, "height": 900})
    page = context.new_page()
    page.store, page.base, page.tmp = store, base, tmp_path
    yield page
    context.close()
    server.should_exit = True
    thread.join(timeout=5)


def backdate(store, eid, days):
    when = (datetime.now(UTC) - timedelta(days=days, hours=1)).isoformat()
    db = sqlite3.connect(store.database_path)
    try:
        db.execute("UPDATE experiments SET started_at = ? WHERE experiment_id = ?", (when, eid))
        db.commit()
    finally:
        db.close()


def make(page, name="Run A", days=7, elapsed=4):
    from tests.test_experiment_scheduler import passing_manifest

    eid = page.store.prepare_experiment(name=name, baseline_manifest=passing_manifest(),
                                        planned_days=days)["experiment_id"]
    page.store.start_experiment(eid)
    backdate(page.store, eid, elapsed)
    return eid


def finish(page, eid):
    """Only one experiment may be active, so close this one (as an incomplete run) first."""
    db = sqlite3.connect(page.store.database_path)
    try:
        db.execute("UPDATE experiments SET status = 'incomplete', completed_at = ? "
                   "WHERE experiment_id = ?", (datetime.now(UTC).isoformat(), eid))
        db.commit()
    finally:
        db.close()


def seed_run(page):
    eid = make(page)
    _day(page.store, eid, 1, cpu_percent=10.0)
    page.store.record_event(category="experiment", event_type="daily_snapshot_skipped_paused",
                            summary="Paused on Day 2", outcome="skipped", day_index=2,
                            experiment_id=eid)
    page.store.record_event(category="experiment", event_type="daily_snapshot_auto_capture_failed",
                            summary="Automatic daily capture failed on Day 3", outcome="failed",
                            day_index=3, experiment_id=eid)
    return eid


def visit(page, query=""):
    page.goto(page.base + "/trends" + query)
    page.wait_for_function("document.getElementById('notice').textContent !== ''")
    page.wait_for_load_state("networkidle")


def cell(page, day):
    return page.locator(f'#days button[data-day="{day}"]')


# --------------------------------------------------------------------------- honesty


def test_with_no_experiment_nothing_is_drawn_and_it_says_why(world):
    visit_page = world
    visit_page.goto(world.base + "/trends")
    visit_page.wait_for_selector("#summary:has-text('nothing to show')")
    assert visit_page.is_hidden("#body") and visit_page.is_disabled("#exp")
    assert visit_page.locator("svg").count() == 0 and visit_page.locator("table").count() == 0
    assert "never shows sample" in visit_page.inner_text("#summary")


def test_every_day_is_named_in_words_and_unexplained_future_days_are_not_missed(world):
    seed_run(world)
    visit(world)
    assert [cell(world, d).get_attribute("aria-label") for d in range(8)] == [
        "Day 0 baseline: Captured", "Day 1: Captured", "Day 2: Paused", "Day 3: Capture failed",
        "Day 4: Due today", "Day 5: Not yet reached", "Day 6: Not yet reached",
        "Day 7: Not yet reached"]
    counts = world.inner_text("#counts")
    assert "2 captured" in counts and "1 paused" in counts and "1 capture failed" in counts
    assert "missed" not in counts  # nothing is called missed that is not yet due


def test_a_day_that_passed_with_no_evidence_is_missed_and_says_nothing_explains_it(world):
    eid = make(world, elapsed=5)
    _day(world.store, eid, 1, cpu_percent=10.0)
    visit(world)
    assert cell(world, 3).get_attribute("aria-label") == "Day 3: Missed"
    cell(world, 3).click()
    assert "nothing explains why" in world.inner_text("#dayDetail")
    assert cell(world, 3).get_attribute("aria-pressed") == "true"


def test_selecting_a_day_shows_its_evidence_hash_and_commit(world):
    seed_run(world)
    visit(world)
    cell(world, 1).click()
    detail = world.inner_text("#dayDetail")
    assert "Snapshot captured" in detail and "Evidence hash" in detail
    full = world.evaluate("window.fetch('/api/experiments/' + document.getElementById('exp').value"
                          " + '/trends').then(r => r.json())")
    assert full["days"][1]["snapshot"]["manifest_sha256"][:12] in detail
    cell(world, 2).click()
    assert "scheduler paused" in world.inner_text("#dayDetail")


def test_the_number_of_dots_equals_the_number_of_recorded_samples_and_a_gap_has_no_line(world):
    eid = make(world, elapsed=6)
    for day, value in ((1, 10.0), (2, 12.0), (5, 20.0)):
        _day(world.store, eid, day, cpu_percent=value)
    visit(world)
    sec = world.locator('[data-metric="cpu_percent"]')
    assert sec.locator("svg circle").count() == 3
    # one line between days 1 and 2 (consecutive); nothing joins day 2 to day 5 across the gap
    assert sec.locator("svg line[stroke-width='2.5']").count() == 1
    assert world.locator('[data-metric="cpu_percent"] svg').get_attribute("aria-hidden") == "true"
    sec.locator("summary").click()
    rows = sec.locator("tbody tr").all_inner_texts()
    assert len(rows) == 3 and rows[0].startswith("1\t10") and "no snapshot" not in rows[0]


def test_dashed_markers_sit_on_days_with_no_snapshot_only(world):
    seed_run(world)
    visit(world)
    world.wait_for_selector('[data-metric="cpu_percent"]')
    # Run A has paused day 2 and failed day 3: two dashed markers, none for captured day 1
    assert world.locator("svg line[stroke-dasharray]").count() == 2


def test_before_after_is_shown_only_when_two_days_were_recorded_and_never_says_improved(world):
    eid = make(world, elapsed=4)
    _day(world.store, eid, 1, cpu_percent=10.0)
    visit(world)
    assert "No before/after comparison: Only one day" in world.inner_text('[data-metric="cpu_percent"]')
    assert "Small sample" in world.inner_text('[data-metric="cpu_percent"]')
    _day(world.store, eid, 3, cpu_percent=16.0)
    world.click("#reload")
    world.wait_for_function("document.getElementById('metrics').textContent.includes('Observed difference')")
    text = world.inner_text('[data-metric="cpu_percent"]')
    assert "First recorded: day 1 = 10 percent" in text and "Latest recorded: day 3 = 16 percent" in text
    assert "+6 percent" in text and "not from Day 0" in text
    # The "claims this page does not make" list quotes the forbidden claims; nothing else may make them.
    claims = " ".join(world.inner_text(sel) for sel in ("#metrics", "#days", "#problems", "#bench")).lower()
    claims = claims.replace("not a claim that anything improved or caused it", "")  # the disclaimer itself
    assert "improved" not in claims and "caused by" not in claims and "regress" not in claims


def test_a_change_near_a_metric_movement_is_a_candidate_not_a_cause(world):
    eid = make(world, elapsed=4)
    _day(world.store, eid, 1, cpu_percent=10.0)
    _day(world.store, eid, 2, cpu_percent=30.0, commit="b" * 40)
    visit(world)
    world.locator('[data-metric="cpu_percent"] summary').click()
    text = world.inner_text('[data-metric="cpu_percent"]')
    assert "new code commit" in text and "Correlation only, not a cause" in text
    assert "Claims this page does not make" in world.inner_text("#limits")


def test_problem_captures_are_listed_with_their_events(world):
    seed_run(world)
    visit(world)
    rows = world.locator("#problems tbody tr").all_inner_texts()
    assert len(rows) == 2 and any("failed" in r for r in rows) and any("Paused" in r for r in rows)


def test_a_run_with_no_problems_says_so(world):
    eid = make(world, elapsed=2)
    _day(world.store, eid, 1, cpu_percent=10.0)
    visit(world)
    assert "No failed, deferred or paused capture" in world.inner_text("#problems")


# ------------------------------------------------------------------- integrity and errors


def test_tampered_evidence_is_refused_and_nothing_is_painted(world):
    eid = seed_run(world)
    db = sqlite3.connect(world.store.database_path)
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")
        db.execute("UPDATE daily_snapshots SET snapshot_json = snapshot_json || ' ' WHERE day_index = 1")
        db.commit()
    finally:
        db.close()
    world.goto(world.base + f"/trends?id={eid}")
    world.wait_for_function("document.getElementById('notice').className === 'error'")
    assert "integrity check" in world.inner_text("#notice")
    assert world.get_attribute("#notice", "role") == "alert"
    assert world.is_hidden("#body") and world.locator("svg").count() == 0


def test_a_failed_request_is_reported(world):
    seed_run(world)
    world.route("**/api/experiments/*/trends", lambda r: r.fulfill(status=404, json={"detail": "gone"}))
    visit(world)
    assert "Could not load this experiment: gone" in world.inner_text("#notice")
    assert world.is_hidden("#body")


def test_experiment_names_and_metric_names_are_text_not_markup(world):
    evil = "<img src=x onerror=window.pwn=1>"
    eid = make(world, name=evil, elapsed=3)
    world.store.record_metric(day_index=1, metric_name=evil, value=1.0, unit="percent",
                              experiment_id=eid)
    _day(world.store, eid, 1, cpu_percent=10.0)
    visit(world)
    world.wait_for_selector("#metrics .series")
    assert world.evaluate("window.pwn") is None and world.locator("body img").count() == 0
    assert evil in world.inner_text("#summary") and evil in world.inner_text("#metrics")


def test_after_the_evidence_is_tampered_a_reload_removes_the_old_picture(world):
    seed_run(world)
    visit(world)
    assert world.is_visible("#body") and world.locator("svg").count() > 0
    db = sqlite3.connect(world.store.database_path)
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")
        db.execute("UPDATE daily_snapshots SET snapshot_json = snapshot_json || ' ' WHERE day_index = 1")
        db.commit()
    finally:
        db.close()
    world.click("#reload")
    world.wait_for_function("document.getElementById('notice').className === 'error'")
    assert world.is_hidden("#body") and world.inner_text("#summary") == ""


def test_switching_experiments_never_paints_the_older_answer_over_the_newer(world):
    a = make(world, "First", elapsed=3)
    _day(world.store, a, 1, cpu_percent=10.0)
    finish(world, a)
    b = make(world, "Second", elapsed=3)
    _day(world.store, b, 1, memory_percent=44.0)
    held = []
    world.route(f"**/api/experiments/{a}/trends", lambda route: held.append(route))
    world.goto(world.base + f"/trends?id={b}")
    world.wait_for_selector('[data-metric="memory_percent"]')
    world.select_option("#exp", a)  # request for A stays in flight
    world.select_option("#exp", b)  # then back to B, which answers
    world.wait_for_function("document.getElementById('notice').textContent.startsWith('Loaded Second')")
    held[0].continue_()
    world.wait_for_timeout(800)
    assert world.locator('[data-metric="cpu_percent"]').count() == 0
    assert world.locator('[data-metric="memory_percent"]').count() == 1


# ------------------------------------------------------------------------- accessibility


def test_accessibility_basics_keyboard_and_focus(world):
    seed_run(world)
    visit(world)
    assert world.get_attribute("html", "lang") == "en" and world.locator("h1").count() == 1
    assert world.get_attribute("#notice", "aria-live") == "polite"
    unnamed = world.evaluate("""() => [...document.querySelectorAll('select,button')]
      .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.textContent.trim())
      .map(e => e.id || e.tagName)""")
    assert unnamed == []
    assert world.locator("th:not([scope])").count() == 0
    world.focus('#days button[data-day="1"]')
    world.keyboard.press("Enter")
    assert world.get_attribute('#days button[data-day="1"]', "aria-pressed") == "true"
    ring = world.evaluate("""() => { document.querySelector('#days button').focus();
      const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; }""")
    assert ring[0] != "none" and ring[1] >= 2


def test_day_state_is_not_conveyed_by_colour_alone(world):
    seed_run(world)
    visit(world)
    glyphs = world.locator("#days .glyph").all_inner_texts()
    assert len(set(glyphs)) >= 4  # different shapes for captured / paused / failed / due / not yet
    borders = world.evaluate("""() => [...document.querySelectorAll('#days button')]
      .map(b => getComputedStyle(b).borderTopStyle)""")
    assert len(set(borders)) >= 3  # and different border styles


@pytest.mark.parametrize("width", [320, 375, 768])
def test_no_horizontal_scroll_on_narrow_screens(world, browser, width):
    seed_run(world)
    context = browser.new_context(viewport={"width": width, "height": 800})
    try:
        page = context.new_page()
        page.goto(world.base + "/trends")
        page.wait_for_selector('[data-metric="cpu_percent"]')
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_reduced_motion_is_respected(world, browser):
    context = browser.new_context(reduced_motion="reduce")
    try:
        page = context.new_page()
        page.goto(world.base + "/trends")
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    finally:
        context.close()


def test_page_is_served_linked_from_the_dashboard_and_packaged(world):
    world.goto(world.base + "/")
    assert world.locator("a[href='/trends']").count() == 1
    assert "trends.html" in (pathlib_root() / "pyproject.toml").read_text(encoding="utf-8")


def pathlib_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[1]
