"""MIA-LIFE-08 (UI half): the /workflows page in Chromium over the real workflow engine.

The page, the FastAPI routes, the workflow engine and the SQLite stores are real.
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
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(tmp_path, browser, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), memory=memory)
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
    context = browser.new_context(viewport={"width": 1100, "height": 900})
    p = context.new_page()
    p.base, p.memory, p.app = f"http://127.0.0.1:{port}", memory, app
    p.posts = []
    p.on("request", lambda r: p.posts.append(r.url) if r.method == "POST" else None)
    yield p
    context.close()
    server.should_exit = True
    thread.join(timeout=5)


def visit(page):
    page.goto(page.base + "/workflows")
    page.wait_for_selector("#list")
    page.wait_for_function("document.querySelectorAll('#addStep:not([disabled])').length === 1")
    page.wait_for_load_state("networkidle")


def add_step(page, capability, **fields):
    page.click("#addStep")
    n = page.locator("#steps fieldset").count() - 1
    box = page.locator("#steps fieldset").nth(n)
    box.locator("select.cap").select_option(capability)
    for name, value in fields.items():
        sel = box.locator(f"[data-name={name}]")
        if sel.evaluate("e => e.tagName") == "SELECT":
            sel.select_option(value)
        else:
            sel.fill(value)
    return box


def plan(page, title="Weekly plan"):
    page.fill("#wfTitle", title)
    page.click("#planBtn")


def wait_detail(page, title):
    page.wait_for_function("(t) => document.getElementById('detailTitle').textContent === t", arg=title)
    page.wait_for_load_state("networkidle")


def wait_status(page, text):
    page.wait_for_function("(t) => document.getElementById('detailStatus').textContent.includes(t)", arg=text)
    page.wait_for_load_state("networkidle")


def build_and_plan(page, title="Weekly plan"):
    add_step(page, "personal.add", title="Draft agenda")
    plan(page, title)
    wait_detail(page, title)


# ------------------------------------------------------------------------------ basics


def test_page_is_served_linked_and_packaged(page):
    from pathlib import Path

    visit(page)
    assert page.title() == "Mia — Workflows" and page.locator("h1").count() == 1
    assert page.locator("nav a[href='/workflows']").count() == 0  # the page's own nav lists others
    page.goto(page.base + "/life")
    assert page.locator("nav a[href='/workflows']").count() == 1
    root = Path(__file__).resolve().parents[1]
    assert "workflows.html" in (root / "pyproject.toml").read_text()


def test_builder_offers_only_the_engines_capabilities(page):
    visit(page)
    add_step(page, "personal.add")
    options = page.locator("#steps select.cap option").all_text_contents()
    assert options == ["notify.submit", "personal.add", "personal.complete", "personal.list"]
    assert not any(o.split(".")[0] in {"computer", "browser", "terminal", "filesystem", "desktop"} for o in options)


def test_empty_states_and_no_steps(page):
    visit(page)
    assert "No workflow yet" in page.locator("#list").inner_text()
    page.fill("#wfTitle", "Nothing")
    page.click("#planBtn")
    assert "at least one step" in page.locator("#notice").inner_text()
    assert page.locator("#notice").get_attribute("role") == "alert"
    assert page.locator("#detail").is_hidden()


# ------------------------------------------------------------------------------ plan and preview


def test_plan_preview_shows_risk_and_approval_and_acts_on_nothing(page):
    visit(page)
    build_and_plan(page)
    text = page.locator("#plan").inner_text()
    assert "Not run yet" in text and "No approval needed" in text and "risk" in text.lower()
    assert page.memory.personal.list(limit=10) == [], "previewing must not act"
    assert "Weekly plan" in page.locator("#list").inner_text()
    assert page.evaluate("document.activeElement.id") == "detailTitle"


def test_run_executes_and_shows_words_events_and_report(page):
    visit(page)
    add_step(page, "personal.add", title="Draft agenda")
    add_step(page, "personal.complete", item_id="${steps.1.item_id}")
    plan(page, "Chain")
    wait_detail(page, "Chain")
    page.click("#runBtn")
    wait_status(page, "Completed")
    assert "2 of 2 steps done" in page.locator("#detailStatus").inner_text()
    assert page.locator("#plan >> text=Succeeded").count() == 2
    assert [i["status"] for i in page.memory.personal.list(limit=10)] == ["done"]
    assert page.locator("#events li").count() >= 2
    assert page.locator("#report").inner_text().strip()
    assert page.locator("#runBtn").is_disabled()


def test_run_button_is_busy_and_cannot_double_run(page):
    visit(page)
    build_and_plan(page)
    held = []
    page.route("**/run", lambda route: held.append(route))
    page.click("#runBtn")
    page.wait_for_function("document.getElementById('runBtn').textContent === 'Running…'")
    assert page.locator("#runBtn").is_disabled() and page.locator("#pauseBtn").is_enabled()
    page.evaluate("document.getElementById('runBtn').click()")
    assert len(held) == 1
    held[0].continue_()
    wait_status(page, "Completed")
    assert len([u for u in page.posts if u.endswith("/run")]) == 1


# ------------------------------------------------------------------------------ refusals


def test_server_refusal_is_shown_verbatim_and_nothing_is_created(page):
    visit(page)
    add_step(page, "personal.list")
    # Force a capability the server must refuse (the select only offers safe ones).
    page.evaluate(
        "() => {const s=document.querySelector('#steps select.cap');"
        "const o=document.createElement('option');o.value='computer.click';o.textContent='computer.click';"
        "s.append(o);s.value='computer.click';s.dispatchEvent(new Event('change'));}")
    page.locator("#steps [data-name=raw]").fill("{}")
    plan(page, "External")
    page.wait_for_function("document.getElementById('notice').className === 'error'")
    msg = page.locator("#notice").inner_text()
    assert msg.startswith("Mia could not make this plan:") and "computer" in msg
    assert page.request.get(page.base + "/api/workflows").json()["items"] == []
    assert page.locator("#steps fieldset").count() == 1, "the draft is kept so nothing is lost"
    assert page.locator("#planBtn").is_enabled()


def test_invalid_number_is_caught_before_sending(page):
    visit(page)
    box = add_step(page, "personal.list")
    box.locator("[data-name=limit]").evaluate("e => { e.type = 'text'; }")
    box.locator("[data-name=limit]").fill("2.5")
    before = len(page.posts)
    plan(page, "Bad number")
    assert "Step 1" in page.locator("#notice").inner_text()
    assert len(page.posts) == before


# ------------------------------------------------------------------------------ failure and retry


def test_interrupted_step_requires_owner_reconciliation_before_retry(page):
    visit(page)
    add_step(page, "personal.add", title="Do not duplicate")
    plan(page, "Unknown outcome")
    wait_detail(page, "Unknown outcome")
    wid = page.locator("#list button").get_attribute("data-id")
    engine = page.app.state.workflow_engine
    engine._set_step(wid, 1, status="interrupted", error="Step timed out; outcome unknown.")
    engine._set_task(wid, status="interrupted", outcome="unknown")
    page.click("#refreshBtn")
    page.wait_for_selector("#plan button[data-reconcile='applied']")
    assert page.locator("#plan button[data-reconcile='not_applied']").count() == 1
    assert page.locator("#plan button[data-retry]").count() == 0

    page.locator("#plan button[data-reconcile='not_applied']").click()
    assert page.locator("#confirm").evaluate("e => e.open")
    assert "did not happen" in page.locator("#confirmTitle").inner_text()
    page.click("#confirmYes")
    wait_status(page, "Paused")
    assert page.locator("#plan button[data-reconcile]").count() == 0
    assert page.locator("#plan button[data-retry]").count() == 0
    assert page.locator("#plan >> text=Not run yet").count() == 1
    page.click("#runBtn")
    wait_status(page, "Completed")
    assert [item["title"] for item in page.memory.personal.list(limit=10)] == ["Do not duplicate"]


def test_failed_step_is_explained_and_retry_is_offered(page):
    visit(page)
    add_step(page, "personal.complete", item_id="nope")
    plan(page, "Will fail")
    wait_detail(page, "Will fail")
    page.click("#runBtn")
    wait_status(page, "Failed")
    assert page.locator("#plan >> text=Problem:").count() == 1
    assert page.locator("#plan >> text=Failed").count() >= 1
    retry = page.locator("#plan button:has-text('Retry step 1')")
    assert retry.count() == 1
    retry.click()
    page.wait_for_function("document.querySelectorAll('#plan button[data-retry]').length === 0")
    assert "Failed" not in page.locator("#detailStatus").inner_text()
    assert page.locator("#plan >> text=Not run yet").count() == 1, "retry only re-arms the step"
    assert page.locator("#runBtn").is_enabled()
    page.click("#runBtn")
    wait_status(page, "Failed")
    assert page.locator("#plan >> text=Problem:").count() == 1, "a repeated failure is explained again"
    assert page.locator("#plan >> text=Risk not known").count() == 1


# ------------------------------------------------------------------------------ cancel and pause


def test_cancel_needs_confirmation_and_keep_changes_nothing(page):
    visit(page)
    build_and_plan(page)
    page.click("#cancelBtn")
    assert page.locator("#confirm").evaluate("e => e.open")
    page.click("#confirmNo")
    assert page.evaluate("document.activeElement.id") == "cancelBtn"
    assert "Planned" in page.locator("#detailStatus").inner_text()
    page.click("#cancelBtn")
    page.click("#confirmYes")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("document.getElementById('detailStatus').textContent.includes('Cancelled')")
    assert page.locator("#runBtn").is_disabled() and page.locator("#cancelBtn").is_disabled()


# ------------------------------------------------------------------------------ builder mechanics


def test_reorder_and_remove_steps_with_buttons_and_focus(page):
    visit(page)
    add_step(page, "personal.add", title="One")
    add_step(page, "personal.add", title="Two")
    page.locator("button[aria-label='Move step 2 up']").click()
    titles = page.locator("#steps [data-name=title]").evaluate_all("els => els.map(e => e.value)")
    assert titles == ["Two", "One"]
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Move step 1 down"
    assert page.locator("button[aria-label='Move step 1 up']").is_disabled()
    page.locator("button[aria-label='Remove step 1']").click()
    titles = page.locator("#steps [data-name=title]").evaluate_all("els => els.map(e => e.value)")
    assert titles == ["One"]
    page.locator("button[aria-label='Remove step 1']").click()
    assert page.locator("#steps fieldset").count() == 0
    assert page.evaluate("document.activeElement.id") == "addStep"


def test_values_survive_reorder_and_arrive_at_the_server_in_order(page):
    visit(page)
    add_step(page, "personal.add", title="Alpha", priority="1")
    add_step(page, "personal.add", title="Beta")
    page.locator("button[aria-label='Move step 2 up']").click()
    plan(page, "Ordered")
    wait_detail(page, "Ordered")
    page.click("#runBtn")
    wait_status(page, "Completed")
    got = sorted(page.memory.personal.list(limit=10), key=lambda i: i["created_at"])
    assert [i["title"] for i in got] == ["Beta", "Alpha"]
    assert [i["priority"] for i in got] == [2, 1]


def test_twenty_steps_is_the_limit(page):
    visit(page)
    for _ in range(20):
        page.click("#addStep")
    assert page.locator("#steps fieldset").count() == 20
    assert page.locator("#addStep").is_disabled()


# ------------------------------------------------------------------------------ safety and a11y


def test_hostile_text_is_inert(page):
    visit(page)
    evil = "<img src=x onerror=window.pwned=1>"
    add_step(page, "personal.add", title=evil)
    plan(page, evil)
    wait_detail(page, evil)
    page.click("#runBtn")
    wait_status(page, "Completed")
    assert page.evaluate("window.pwned") is None
    assert page.locator("#plan img, #list img, #events img").count() == 0
    assert evil in page.locator("#list").inner_text()


def test_stale_detail_response_does_not_overwrite_newer(page):
    visit(page)
    build_and_plan(page, "First")
    add_step(page, "personal.add", title="Second item")
    plan(page, "Second")
    wait_detail(page, "Second")
    first = page.locator("#list button:has-text('First')").get_attribute("data-id")
    second = page.locator("#list button:has-text('Second')").get_attribute("data-id")
    held = []
    page.route(f"**/api/workflows/{first}", lambda route: held.append(route))
    page.click("#list button:has-text('First')")
    page.wait_for_function("1", timeout=1000)
    page.click(f"#list button[data-id='{second}']")
    wait_detail(page, "Second")
    assert held
    held[0].continue_()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    assert page.locator("#detailTitle").inner_text() == "Second"
    assert "Second item" in page.locator("#plan").inner_text()


def test_keyboard_focus_visible_and_320px_reflow_without_overflow(page):
    visit(page)
    build_and_plan(page)
    page.set_viewport_size({"width": 320, "height": 800})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.keyboard.press("Tab")
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none"
    assert page.locator("button:visible").evaluate_all("els => els.every(e => e.getBoundingClientRect().height >= 40)")


def test_status_is_text_not_colour_only(page):
    visit(page)
    build_and_plan(page)
    assert "Planned" in page.locator("#detailStatus").inner_text()
    assert all(t.strip() for t in page.locator("#plan .tag").all_text_contents())
