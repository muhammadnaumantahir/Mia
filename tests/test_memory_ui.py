"""Real-browser (Chromium via Playwright) tests of the /memory management page.

They drive Mia's real page JavaScript against the real FastAPI app and SQLite store. They check
behaviour (filtering, approve, delete, settings persistence, mobile layout) rather than pixels.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("memory-ui")
    memory = MemoryStore(tmp / "memory.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp / "obs.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()),
        memory=memory,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    assert uv.started
    yield f"http://127.0.0.1:{port}", memory
    uv.should_exit = True
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


@pytest.fixture()
def page(browser, server):
    context = browser.new_context(viewport={"width": 1100, "height": 800})
    page = context.new_page()
    yield page
    context.close()


def _add(memory, title, body, **kw):
    result = memory.structured.add(
        record_type=kw.pop("record_type", "fact"), title=title, body=body, **kw
    )
    return result[0] if isinstance(result, tuple) else result


def _status(page, text, timeout=5000):
    page.wait_for_function(
        "t => document.getElementById('status').textContent.includes(t)", arg=text, timeout=timeout
    )


def test_page_served_and_lists_memories(page, server):
    base, memory = server
    _add(memory, "Likes green tea", "The owner likes green tea.", provenance="owner_provided")
    _add(memory, "Mia noticed hiking", "Maybe likes hiking.", provenance="observed")
    page.goto(base + "/memory")
    page.wait_for_selector("article.card")
    text = page.inner_text("#view")
    assert "Likes green tea" in text and "You confirmed" in text
    assert "unconfirmed" in text  # observed one is labelled as not confirmed
    assert "Why saved" in text


def test_search_and_filter(page, server):
    base, memory = server
    _add(memory, "Quokka fact", "Quokkas are small marsupials.", provenance="owner_provided")
    _add(memory, "Ship decision", "We decided to ship on Friday.", record_type="decision",
         provenance="owner_provided")
    page.goto(base + "/memory")
    page.wait_for_selector("article.card")
    page.fill("#q", "quokka")
    page.wait_for_function("document.querySelectorAll('article.card').length === 1")
    assert "Quokka fact" in page.inner_text("#mlist")
    page.fill("#q", "")
    page.select_option("#ftype", "decision")
    page.wait_for_function(
        "[...document.querySelectorAll('article.card h3')].every(h => h.textContent.includes('Ship'))"
    )
    assert "Ship decision" in page.inner_text("#mlist")


def test_pending_suggestion_can_be_approved_and_rejected(page, server):
    base, memory = server
    pending = _add(memory, "Suggested cat", "Owner has a cat named Pixel.", status="pending",
                   provenance="observed")
    rejected = _add(memory, "Suggested dog", "Owner has a dog.", status="pending",
                    provenance="observed")
    page.goto(base + "/memory#suggestions")
    page.wait_for_selector("article.card")
    card = page.locator("article.card", has_text="Suggested cat")
    card.get_by_role("button", name="Approve").click()
    _status(page, "Approved")
    assert memory.structured.history(pending)[-1]["status"] == "active"
    page.locator("article.card", has_text="Suggested dog").get_by_role("button", name="Reject").click()
    _status(page, "Rejected")
    assert memory.structured.history(rejected)[-1]["status"] == "rejected"


def test_delete_removes_record_and_index(page, server):
    base, memory = server
    rid = _add(memory, "Zebracorn secret", "xyzzyplugh unique token", provenance="owner_provided")
    page.goto(base + "/memory?record=" + rid)
    page.wait_for_selector("#rec-" + rid)
    card = page.locator("#rec-" + rid)
    card.get_by_role("button", name="Delete").click()
    page.get_by_role("button", name="Delete permanently").click()
    _status(page, "Deleted")
    assert memory.structured.list(query="xyzzyplugh") == []
    assert page.locator("#rec-" + rid).count() == 0


def test_settings_persist(page, server):
    base, memory = server
    page.goto(base + "/memory#controls")
    page.wait_for_selector("input[name=mode]")
    page.check("input[name=mode][value=off]")
    _status(page, "Saved")
    assert memory.settings.get("capture_mode") == "off"
    page.uncheck("#t_retrieval_enabled")
    _status(page, "Saved")
    assert memory.settings.get("retrieval_enabled") is False
    page.reload()
    page.wait_for_selector("input[name=mode]")
    assert page.is_checked("input[name=mode][value=off]")
    assert not page.is_checked("#t_retrieval_enabled")
    page.check("input[name=mode][value=approval]")
    page.check("#t_retrieval_enabled")
    _status(page, "Saved")


def test_identity_edit_and_history(page, server):
    base, memory = server
    page.goto(base + "/memory#identity")
    page.wait_for_selector("#i_purpose")
    page.fill("#i_purpose", "Be a calm planning partner.")
    page.fill("#i_reason", "ui test")
    page.get_by_role("button", name="Save identity").click()
    _status(page, "Identity saved")
    assert memory.identity.active()["profile"]["purpose"] == "Be a calm planning partner."
    page.wait_for_selector("text=Version history")
    assert "(current)" in page.inner_text("#view")


def test_page_text_is_not_interpreted_as_html(page, server):
    base, memory = server
    _add(memory, "<img src=x onerror=window.__pwned=1>", "<script>window.__pwned=1</script>",
         provenance="owner_provided")
    page.goto(base + "/memory")
    page.wait_for_selector("article.card")
    assert page.evaluate("window.__pwned") is None
    assert "<img src=x" in page.inner_text("#mlist")


def test_mobile_layout_has_no_horizontal_scroll(browser, server):
    base, _ = server
    context = browser.new_context(viewport={"width": 375, "height": 740}, is_mobile=True)
    page = context.new_page()
    page.goto(base + "/memory")
    page.wait_for_selector("article.card")
    for tab in ("controls", "identity", "care", "activity"):
        page.goto(base + "/memory#" + tab)
        page.reload()
        page.wait_for_timeout(300)
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
        ), tab
    context.close()


def test_activity_tab_shows_audit_without_memory_text(page, server):
    base, memory = server
    _add(memory, "Audit probe", "uniquebodytext12345", provenance="owner_provided")
    page.goto(base + "/memory#activity")
    page.wait_for_selector(".audit")
    assert "uniquebodytext12345" not in page.inner_text("#view")
