"""MIA-LIFE-04 (UI half): the /projects page, driven in Chromium against the real project API.

Nothing is mocked: the page, the FastAPI routes and the SQLite stores are the real ones. The
isolation checks read what is actually painted, so a leak of another project's text into the
DOM is caught even if the API behaved.
"""

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("projects-page")
    os.environ["MIA_DATA_DIR"] = str(tmp / "data")
    memory = MemoryStore(tmp / "memory.sqlite3")
    app = create_app(store=ObservatoryStore(tmp / "obs.sqlite3"), memory=memory)
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
    yield f"http://127.0.0.1:{port}", memory
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


def reset(memory):
    for item in memory.personal.list(limit=500):
        memory.personal.delete(item["item_id"])
    for record in memory.structured.list(record_type="decision", limit=500):
        memory.structured.delete(record["record_id"])
    for project in memory.projects.list():
        memory.projects.delete(project["name"])
    for convo in memory.list_conversations(200):
        memory.delete_conversation(convo["conversation_id"])


def seed(memory):
    memory.projects.create("Atlas", goal="Ship the atlas importer")
    memory.projects.create("Borealis", goal="Ship the borealis exporter", status="paused")
    memory.projects.add_milestone("Atlas", "Atlas alpha")
    memory.projects.add_milestone("Borealis", "Borealis beta")
    memory.personal.add(kind="task", title="atlas task", project="Atlas")
    memory.personal.add(kind="task", title="borealis task", project="Borealis")
    memory.personal.add(kind="task", title="loose task")
    memory.structured.add(record_type="decision", title="Atlas db", body="Atlas uses PostgreSQL.",
                          project="Atlas")
    memory.structured.add(record_type="decision", title="Borealis db", body="Borealis uses MySQL.",
                          project="Borealis")
    for cid, project, temporary in (("conv-atlas-000001", "Atlas", False),
                                    ("conv-atlas-000002", "Atlas", True),
                                    ("conv-borealis-00001", "Borealis", False)):
        memory.ensure_conversation(cid)
        memory.append_message(cid, "user", f"hello {cid}")
        memory.set_conversation_project(cid, project)
        if temporary:
            memory.set_conversation_temporary(cid, True)


@pytest.fixture
def page(live, browser):
    base, memory = live
    reset(memory)
    context = browser.new_context(viewport={"width": 1100, "height": 900})
    p = context.new_page()
    p.memory, p.base = memory, base
    yield p
    context.close()


def visit(page, query=""):
    page.goto(page.base + "/projects" + query)
    page.wait_for_selector("#projects")
    page.wait_for_load_state("networkidle")


def open_project(page, name):
    page.click(f'#projects button[data-name="{name}"]')
    page.wait_for_function("(n) => document.getElementById('detailTitle').textContent === n", arg=name)
    page.wait_for_load_state("networkidle")


def body_text(page):
    return page.inner_text("#detail")


# ------------------------------------------------------------------------------- listing


def test_page_is_served_linked_and_packaged(page):
    visit(page)
    assert page.title() == "Mia — Projects"
    page.goto(page.base + "/life")
    assert page.locator("nav a[href='/projects']").count() == 1
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "projects.html" in text and "tasks.html" in text


def test_lists_registered_and_discovered_projects_with_counts_in_words(page):
    seed(page.memory)
    page.memory.personal.add(kind="task", title="orphan", project="Zephyr")  # never registered
    visit(page)
    names = page.locator("#projects button").evaluate_all("els => els.map(e => e.dataset.name)")
    assert names == ["Atlas", "Borealis", "Zephyr"]
    atlas = page.inner_text('#projects button[data-name="Atlas"]')
    assert "Active" in atlas and "1 open task" in atlas and "1 decision" in atlas and "1 conversation" in atlas
    assert "Paused" in page.inner_text('#projects button[data-name="Borealis"]')
    assert "Not set up" in page.inner_text('#projects button[data-name="Zephyr"]')


def test_empty_state_explains_how_projects_appear(page):
    visit(page)
    assert "No projects yet" in page.inner_text("#projects")


# ---------------------------------------------------------------------------- isolation


def test_a_project_page_shows_only_its_own_material(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    text = body_text(page)
    assert page.input_value("#editGoal") == "Ship the atlas importer"  # the goal sits in a field
    for own in ("Atlas alpha", "atlas task", "Atlas uses PostgreSQL"):
        assert own in text
    everything = text + page.input_value("#editGoal")
    for foreign in ("borealis", "Borealis", "MySQL", "loose task"):
        assert foreign not in everything, foreign
    assert "1 conversation" in page.inner_text("#countsLine")  # the temporary chat is not counted
    assert "conv-atlas-000002" not in text


def test_switching_projects_replaces_everything_and_leaves_nothing_behind(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    open_project(page, "Borealis")
    text = body_text(page)
    assert "Borealis beta" in text and "borealis task" in text
    for stale in ("Atlas", "atlas", "PostgreSQL"):
        assert stale not in text, stale


def test_a_slow_response_for_the_previous_project_never_paints_over_the_new_one(page):
    seed(page.memory)
    held = []
    page.route("**/api/projects/Atlas", lambda route: held.append(route))  # Atlas stays in flight
    visit(page)
    page.click('#projects button[data-name="Atlas"]')
    page.wait_for_function("document.getElementById('detailTitle') !== null")
    page.click('#projects button[data-name="Borealis"]')  # clicked later, answers first
    page.wait_for_function("document.getElementById('detailTitle').textContent === 'Borealis'")
    assert len(held) == 1
    held[0].continue_()  # now the stale Atlas answer arrives
    page.wait_for_timeout(800)
    assert page.inner_text("#detailTitle") == "Borealis"
    assert "Atlas" not in body_text(page) and "atlas" not in body_text(page)
    assert page.input_value("#editGoal") == "Ship the borealis exporter"


def test_deep_link_opens_the_named_project(page):
    seed(page.memory)
    visit(page, "?name=Borealis")
    page.wait_for_function("document.getElementById('detailTitle').textContent === 'Borealis'")
    assert "Borealis beta" in body_text(page)
    assert "name=Borealis" in page.url


def test_html_in_names_goals_and_titles_is_text_not_markup(page):
    evil = "<img src=x onerror=window.__pwn=1>"
    page.memory.projects.create("Hack", goal=evil)
    page.memory.projects.add_milestone("Hack", evil)
    visit(page)
    open_project(page, "Hack")
    assert page.evaluate("window.__pwn") is None
    assert evil in page.input_value("#editGoal") and evil in page.inner_text("#milestones")
    assert page.locator("#detail img").count() == 0


# ------------------------------------------------------------------------------ editing


def test_create_project_writes_to_the_store_and_opens_it(page):
    visit(page)
    page.fill("#addName", "Cygnus")
    page.fill("#addGoal", "Reach the swan")
    page.select_option("#addStatus", "paused")
    page.click("#addForm button[type=submit]")
    page.wait_for_function("document.getElementById('detailTitle').textContent === 'Cygnus'")
    stored = page.memory.projects.get("Cygnus")
    assert stored["goal"] == "Reach the swan" and stored["status"] == "paused"
    assert "Status: Paused" in page.inner_text("#detailTags")
    assert page.evaluate("document.activeElement.id") == "detailTitle"


def test_create_rejects_a_slash_and_a_duplicate_with_a_clear_message(page):
    page.memory.projects.create("Atlas")
    visit(page)
    page.fill("#addName", "a/b")
    page.click("#addForm button[type=submit]")
    assert "cannot contain" in page.inner_text("#notice") and page.get_attribute("#notice", "role") == "alert"
    assert page.memory.projects.get("a/b") is None
    page.fill("#addName", "atlas")
    page.click("#addForm button[type=submit]")
    page.wait_for_function("document.getElementById('notice').textContent.includes('Could not create')")
    assert page.get_attribute("#notice", "role") == "alert"


def test_goal_and_status_save_to_the_store(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    page.fill("#editGoal", "New direction")
    page.select_option("#editStatus", "completed")
    page.click("#editForm button[type=submit]")
    page.wait_for_function("document.getElementById('detailTags').textContent.includes('Completed')")
    stored = page.memory.projects.get("Atlas")
    assert stored["goal"] == "New direction" and stored["status"] == "completed"


def test_milestones_can_be_added_completed_reopened_edited_and_deleted(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    page.fill("#msTitle", "Atlas beta")
    page.fill("#msDue", "2031-03-04T10:00")
    page.click("#msForm button[type=submit]")
    page.wait_for_selector("#milestones li:has-text('Atlas beta')")
    assert "Due" in page.inner_text("#milestones li:has-text('Atlas beta')")
    milestones = {m["title"]: m for m in page.memory.projects.milestones("Atlas")}
    assert milestones["Atlas beta"]["due_at"].startswith("2031-")

    row = "#milestones li:has-text('Atlas beta')"
    page.click(f"{row} button:has-text('Mark done')")
    page.wait_for_selector(f"{row} .tag:text-is('Done')")
    assert {m["title"]: m["status"] for m in page.memory.projects.milestones("Atlas")}["Atlas beta"] == "done"
    page.click(f"{row} button:has-text('Reopen')")
    page.wait_for_selector(f"{row} .tag:text-is('Open')")

    page.click(f"{row} button:has-text('Edit')")
    page.fill(f"{row} form input >> nth=0", "Atlas beta two")
    page.fill(f"{row} form input >> nth=1", "")
    page.click(f"{row} form button[type=submit]")
    page.wait_for_selector("#milestones li:has-text('Atlas beta two')")
    changed = {m["title"]: m for m in page.memory.projects.milestones("Atlas")}["Atlas beta two"]
    assert changed["due_at"] is None  # clearing the date really clears it

    page.click("#milestones li:has-text('Atlas beta two') button:has-text('Delete')")
    assert page.is_visible("#confirm")
    page.click("#confirmYes")
    page.wait_for_function("!document.getElementById('milestones').textContent.includes('Atlas beta two')")
    assert "Atlas beta two" not in {m["title"] for m in page.memory.projects.milestones("Atlas")}


def test_cancelling_a_delete_changes_nothing_and_returns_focus(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    page.click("#milestones button:has-text('Delete')")
    page.click("#confirmNo")
    assert [m["title"] for m in page.memory.projects.milestones("Atlas")] == ["Atlas alpha"]
    assert "Delete" in page.evaluate("document.activeElement.getAttribute('aria-label')")
    page.click("#deleteProject")
    page.keyboard.press("Escape")
    assert page.memory.projects.get("Atlas") is not None


def test_deleting_a_project_keeps_its_tasks_decisions_and_chats_and_says_so(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    page.click("#deleteProject")
    dialog = page.inner_text("#confirm")
    assert "1 milestone" in dialog and "are kept" in dialog
    page.click("#confirmYes")
    page.wait_for_function("document.getElementById('detail').hidden")
    assert page.memory.projects.get("Atlas") is None
    assert [t["title"] for t in page.memory.personal.list(kind="task", project="Atlas")] == ["atlas task"]
    assert "kept" in page.inner_text("#notice")
    assert "Not set up" in page.inner_text('#projects button[data-name="Atlas"]')  # still discovered


def test_an_unregistered_project_can_be_set_up_without_touching_its_material(page):
    page.memory.personal.add(kind="task", title="orphan task", project="Zephyr")
    visit(page)
    open_project(page, "Zephyr")
    assert page.is_visible("#unregistered") and not page.is_visible("#registered")
    assert "orphan task" in page.inner_text("#tasks")
    page.click("#setup")
    page.wait_for_selector("#registered:not([hidden])")
    assert page.memory.projects.get("Zephyr") is not None
    assert [t["title"] for t in page.memory.personal.list(kind="task", project="Zephyr")] == ["orphan task"]


def test_a_name_with_a_slash_is_explained_not_silently_broken(page):
    page.memory.personal.add(kind="task", title="slash task", project="front/back")
    visit(page)
    page.click('#projects button[data-name="front/back"]')
    page.wait_for_function("document.getElementById('notice').className === 'error'")
    assert "contains a" in page.inner_text("#notice") and "/" in page.inner_text("#notice")
    assert "Cannot be opened here" in page.inner_text("#detailTags")
    assert page.is_hidden("#registered") and page.is_hidden("#material")


def test_failures_are_reported_not_swallowed(page):
    seed(page.memory)
    visit(page)
    open_project(page, "Atlas")
    page.route("**/api/projects/Atlas/milestones", lambda r: r.fulfill(status=500, body="{}"))
    page.fill("#msTitle", "will fail")
    page.click("#msForm button[type=submit]")
    page.wait_for_function("document.getElementById('notice').className === 'error'")
    assert "Could not add the milestone" in page.inner_text("#notice")
    assert "will fail" not in {m["title"] for m in page.memory.projects.milestones("Atlas")}


def test_load_failure_is_reported(page):
    page.route("**/api/projects", lambda r: r.fulfill(status=500, json={"detail": "boom"}))
    page.goto(page.base + "/projects")
    page.wait_for_function("document.getElementById('notice').textContent.includes('Could not load your projects: boom')")
    assert page.get_attribute("#notice", "role") == "alert"


# ------------------------------------------------------------------------- accessibility


def test_accessibility_basics_and_keyboard_operation(page):
    seed(page.memory)
    visit(page)
    assert page.get_attribute("html", "lang") == "en" and page.locator("h1").count() == 1
    unnamed = page.evaluate("""() => [...document.querySelectorAll('input,select,textarea,button')]
      .filter(e => !e.hidden && e.offsetParent !== null)
      .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label') && !e.textContent.trim())
      .map(e => e.id || e.tagName)""")
    assert unnamed == []
    page.focus('#projects button[data-name="Atlas"]')
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('detailTitle').textContent === 'Atlas'")
    assert page.evaluate("document.activeElement.id") == "detailTitle"
    assert page.get_attribute('#projects button[data-name="Atlas"]', "aria-current") == "true"
    assert page.get_attribute("#notice", "aria-live") == "polite"
    ring = page.evaluate("""() => { document.querySelector('#projects button').focus();
      const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; }""")
    assert ring[0] != "none" and ring[1] >= 2


def test_status_and_overdue_are_stated_in_words_not_only_colour(page):
    page.memory.projects.create("Late")
    page.memory.projects.add_milestone("Late", "Past due", due_at="2001-01-01T00:00:00+00:00")
    visit(page)
    open_project(page, "Late")
    assert "Overdue" in page.inner_text("#milestones")
    assert "Status: Active" in page.inner_text("#detailTags")


@pytest.mark.parametrize("width", [320, 375, 768])
def test_no_horizontal_scroll_on_narrow_screens(live, browser, width):
    base, memory = live
    reset(memory)
    seed(memory)
    memory.projects.create("A-very-long-project-name-" + "x" * 36)
    context = browser.new_context(viewport={"width": width, "height": 800})
    try:
        p = context.new_page()
        p.goto(base + "/projects?name=Atlas")
        p.wait_for_function("document.getElementById('detailTitle').textContent === 'Atlas'")
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    finally:
        context.close()


def test_reduced_motion_is_respected(live, browser):
    base, _ = live
    context = browser.new_context(reduced_motion="reduce")
    try:
        p = context.new_page()
        p.goto(base + "/projects")
        assert p.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert p.evaluate("getComputedStyle(document.body).transitionDuration") in ("0s", "0.001s")
    finally:
        context.close()
