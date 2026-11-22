"""MIA-LIFE-03: the /tasks workspace page, driven in Chromium against the real personal-items API.

Nothing is mocked: the page, the FastAPI routes and the SQLite personal store are the real ones, so
a change made in the page is checked against the same source of truth Mia's conversations use.
"""

import socket
import threading
import time

import pytest
import uvicorn

from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("tasks-workspace")
    import os

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


@pytest.fixture
def page(live, browser):
    base, memory = live
    for item in memory.personal.list(limit=500):
        memory.personal.delete(item["item_id"])
    context = browser.new_context(viewport={"width": 1000, "height": 900})
    p = context.new_page()
    p.memory = memory
    p.base = base
    yield p
    context.close()


def titles(page):
    return [t.strip() for t in page.locator("li.item .title").all_inner_texts()]


def open_page(page):
    page.goto(page.base + "/tasks")
    page.wait_for_selector("#items")


def test_page_lists_items_from_the_shared_store_and_escapes_text(page):
    page.memory.personal.add(kind="task", title="<img src=x onerror=alert(1)> pay rent", details="a<b>c")
    page.memory.personal.add(kind="note", title="Plain note", details="")
    open_page(page)
    page.select_option("#fKind", "")
    page.wait_for_function("document.querySelectorAll('li.item').length===2")
    assert "<img src=x onerror=alert(1)> pay rent" in titles(page)
    assert page.locator("li.item img").count() == 0, "titles must be text, never markup"
    assert page.locator("li.item .details", has_text="a<b>c").count() == 1


def test_add_form_writes_to_the_shared_store(page):
    open_page(page)
    page.fill("#addTitle", "Call the bank")
    page.select_option("#addPriority", "1")
    page.fill("#addProject", "home")
    page.click("#addForm button[type=submit]")
    page.wait_for_function("document.querySelectorAll('li.item').length===1")
    stored = page.memory.personal.list(limit=10)
    assert [(i["title"], i["priority"], i["project"], i["kind"]) for i in stored] == [
        ("Call the bank", 1, "home", "task")
    ]
    assert "Added: Call the bank" in page.inner_text("#notice")
    assert page.evaluate("document.activeElement.id") == "addTitle"


def test_duplicate_is_reported_and_can_be_added_deliberately(page):
    page.memory.personal.add(kind="task", title="Buy milk")
    open_page(page)
    page.fill("#addTitle", "Buy milk")
    page.click("#addForm button[type=submit]")
    page.wait_for_function("document.getElementById('notice').textContent.includes('very similar')")
    assert len(page.memory.personal.list(limit=10)) == 1
    page.click("#addForm button[type=submit]")
    page.wait_for_function("document.querySelectorAll('li.item').length===2")


def test_complete_and_reopen_update_the_store(page):
    item_id = page.memory.personal.add(kind="task", title="Water plants")
    open_page(page)
    page.click("button[aria-label='Complete: Water plants']")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert page.memory.personal.get(item_id)["status"] == "done"
    page.select_option("#fStatus", "done")
    page.wait_for_selector("button[aria-label='Reopen: Water plants']")
    page.click("button[aria-label='Reopen: Water plants']")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert page.memory.personal.get(item_id)["status"] == "open"


def test_edit_changes_fields_and_can_clear_the_due_date(page):
    item_id = page.memory.personal.add(
        kind="task", title="Draft report", due_at="2030-01-05T09:00:00+00:00", project="work"
    )
    open_page(page)
    page.click("button[aria-label='Edit: Draft report']")
    form = page.locator("form.edit")
    form.locator("input").first.fill("Draft the report")
    form.locator("input[type=datetime-local]").fill("")
    page.click("form.edit button[type=submit]")
    page.wait_for_function("document.querySelector('li.item .title').textContent==='Draft the report'")
    stored = page.memory.personal.get(item_id)
    assert stored["title"] == "Draft the report"
    assert stored["due_at"] is None
    assert stored["project"] == "work"


def test_single_delete_needs_confirmation_and_cancelling_keeps_the_item(page):
    item_id = page.memory.personal.add(kind="task", title="Keep me")
    open_page(page)
    page.click("button[aria-label='Delete: Keep me']")
    assert page.locator("dialog[open]").count() == 1
    assert page.evaluate("document.activeElement.id") == "confirmNo", "safe choice has focus"
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('confirm').open")
    assert page.memory.personal.get(item_id) is not None
    page.click("button[aria-label='Delete: Keep me']")
    page.click("#confirmYes")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert page.memory.personal.get(item_id) is None


def test_bulk_delete_lists_what_will_go_and_only_deletes_selected(page):
    ids = [page.memory.personal.add(kind="task", title=f"Chore {n}") for n in range(12)]
    open_page(page)
    page.wait_for_function("document.querySelectorAll('li.item').length===12")
    page.check("#selectAll")
    page.uncheck("li.item[data-id='" + ids[0] + "'] input[type=checkbox]")
    assert page.inner_text("#selCount") == "11 selected"
    page.click("#bulkDelete")
    dialog = page.locator("dialog[open]")
    assert "Delete 11 items?" in dialog.inner_text()
    assert dialog.locator("li").count() == 11  # 10 titles + "and 1 more"
    assert "and 1 more" in dialog.inner_text()
    assert len(page.memory.personal.list(limit=50)) == 12, "nothing is deleted before confirming"
    page.click("#confirmYes")
    page.wait_for_function("document.querySelectorAll('li.item').length===1")
    assert [i["item_id"] for i in page.memory.personal.list(limit=50)] == [ids[0]]


def test_bulk_cancel_is_confirmed_and_reversible(page):
    ids = [page.memory.personal.add(kind="task", title=f"Maybe {n}") for n in range(3)]
    open_page(page)
    page.check("#selectAll")
    page.click("#bulkCancel")
    assert "Cancel 3 items?" in page.locator("dialog[open]").inner_text()
    page.click("#confirmNo")
    assert all(page.memory.personal.get(i)["status"] == "open" for i in ids)
    page.click("#bulkCancel")
    page.click("#confirmYes")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert all(page.memory.personal.get(i)["status"] == "cancelled" for i in ids)
    page.select_option("#fStatus", "cancelled")
    page.wait_for_function("document.querySelectorAll('li.item').length===3")
    page.check("#selectAll")
    page.click("#bulkReopen")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert all(page.memory.personal.get(i)["status"] == "open" for i in ids)


def test_bulk_buttons_are_disabled_until_something_is_selected(page):
    page.memory.personal.add(kind="task", title="One")
    open_page(page)
    page.wait_for_selector("li.item")
    for button in ("bulkComplete", "bulkReopen", "bulkCancel", "bulkDelete"):
        assert page.is_disabled("#" + button)
    page.check("li.item input[type=checkbox]")
    assert page.is_enabled("#bulkDelete")


def test_overdue_is_stated_in_words_not_only_colour(page):
    page.memory.personal.add(kind="task", title="Late one", due_at="2020-01-01T00:00:00+00:00")
    open_page(page)
    page.wait_for_selector("li.item")
    assert "Overdue" in page.inner_text("li.item .meta")


def test_accessibility_basics_and_keyboard_operation(page):
    page.memory.personal.add(kind="task", title="Keyboard task")
    open_page(page)
    page.wait_for_selector("li.item")
    assert page.get_attribute("html", "lang") == "en"
    assert page.get_attribute("#notice", "aria-live") == "polite"
    assert page.get_attribute("#items", "aria-labelledby") == "listHeading"
    # every control has an accessible name
    unnamed = page.evaluate(
        """() => [...document.querySelectorAll('button, input, select, textarea')]
            .filter(e => !(e.getAttribute('aria-label') || (e.labels && e.labels.length) || e.textContent.trim()))
            .map(e => e.outerHTML.slice(0, 80))"""
    )
    assert unnamed == []
    # a task can be completed with the keyboard alone
    page.focus("button[aria-label='Complete: Keyboard task']")
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelectorAll('li.item').length===0")
    assert page.evaluate("document.activeElement.id") == "listHeading"


def test_narrow_screen_has_no_horizontal_scroll(page):
    page.memory.personal.add(kind="task", title="A fairly long task title that must wrap instead of overflowing " * 2)
    page.set_viewport_size({"width": 360, "height": 740})
    open_page(page)
    page.wait_for_selector("li.item")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_reduced_motion_is_respected(page):
    page.emulate_media(reduced_motion="reduce")
    open_page(page)
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    assert page.evaluate("getComputedStyle(document.body).animationName") in {"none", ""}


def test_load_failure_is_reported(page):
    open_page(page)
    page.route("**/api/personal/items?*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"detail":"boom"}'))
    page.select_option("#fKind", "note")
    page.wait_for_function("document.getElementById('notice').textContent.includes('Could not load your list: boom')")
    assert page.get_attribute("#notice", "role") == "alert"


def test_page_is_served_and_linked_from_life(page):
    open_page(page)
    assert page.title() == "Mia — Tasks"
    page.goto(page.base + "/life")
    assert page.locator("nav a[href='/tasks']").count() == 1
