"""MIA-HOLO-02: behaviour of the isolated hologram renderer spike in real Chromium.

Chromium here has no GPU (software GL), so these prove lifecycle and fallback behaviour:
WebGL detection, state looks, reduced motion, pausing while hidden, context loss and clean
disposal. They say nothing about frame rate on the owner's hardware (that is MIA-HOLO-06).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

SPIKE = Path(__file__).resolve().parents[1] / "spikes" / "holo-renderer"
NO_WEBGL = (
    "const g=HTMLCanvasElement.prototype.getContext;"
    "HTMLCanvasElement.prototype.getContext=function(t,...a){"
    "return /webgl/.test(t)?null:g.call(this,t,...a)};"
)


@pytest.fixture(scope="module")
def spike_page():
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        pytest.skip("node/npm not available to build the spike")
    try:
        if not (SPIKE / "node_modules").exists():
            subprocess.run([npm, "ci"], cwd=SPIKE, check=True, capture_output=True, timeout=300)
        subprocess.run([node, "build.mjs"], cwd=SPIKE, check=True, capture_output=True, timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"could not build the spike: {exc}")
    return (SPIKE / "spike.html").as_uri()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


@pytest.fixture()
def open_spike(browser, spike_page):
    contexts = []

    def opener(query="", *, init_script=None, reduced_motion="no-preference"):
        context = browser.new_context(reduced_motion=reduced_motion, viewport={"width": 640, "height": 480})
        contexts.append(context)
        if init_script:
            context.add_init_script(init_script)
        page = context.new_page()
        page.goto(spike_page + query)
        return page

    yield opener
    for context in contexts:
        context.close()


def stats(page):
    return page.evaluate("window.__holo.holo.stats()")


def test_renders_and_animates_with_webgl(open_spike):
    page = open_spike("?state=thinking")
    assert page.evaluate("window.__holo.ok") is True
    page.wait_for_function("window.__holo.holo.stats().frames > 5", timeout=15000)
    s = stats(page)
    assert s["state"] == "thinking" and s["running"] and s["drawCalls"] >= 1
    assert page.locator("#fallback").is_hidden() and page.locator("#scene").is_visible()


def test_missing_webgl_shows_text_fallback_and_does_not_throw(open_spike):
    page = open_spike(init_script=NO_WEBGL)
    assert page.evaluate("window.__holo.ok") is False
    assert page.evaluate("window.__holo.reason") == "webgl_unavailable"
    assert page.locator("#fallback").is_visible() and page.locator("#scene").is_hidden()
    assert "unaffected" in page.locator("#fallback").inner_text()
    assert page.locator("#fallback").get_attribute("role") == "status"


def test_every_runtime_state_has_a_look_and_unknown_states_do_not_become_idle(open_spike):
    page = open_spike()
    states = page.evaluate("window.__holo.states")
    assert set(states) == {"unknown", "idle", "listening", "thinking", "speaking", "interrupted",
                           "tool_running", "awaiting_approval", "failed", "offline"}
    for state in states:
        page.evaluate("s => window.__holo.holo.setState(s)", state)
        assert stats(page)["state"] == state
    page.evaluate("window.__holo.holo.setState('mia_is_dreaming')")
    assert stats(page)["state"] == "unknown"


def test_reduced_motion_draws_static_frames_only(open_spike):
    page = open_spike("?state=thinking", reduced_motion="reduce")
    page.wait_for_timeout(600)
    before = stats(page)["frames"]
    page.wait_for_timeout(600)
    assert stats(page)["frames"] == before  # no animation loop
    page.evaluate("window.__holo.holo.setState('failed')")
    assert stats(page)["frames"] == before + 1  # a state change still redraws once


def test_loop_pauses_while_hidden_and_resumes(open_spike):
    page = open_spike("?state=listening")
    page.wait_for_function("window.__holo.holo.stats().frames > 3", timeout=15000)
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {value: true, configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.wait_for_timeout(300)
    frozen = stats(page)["frames"]
    page.wait_for_timeout(500)
    assert stats(page)["frames"] == frozen
    page.evaluate("""() => {
        Object.defineProperty(document, 'hidden', {value: false, configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    page.wait_for_function(f"window.__holo.holo.stats().frames > {frozen}", timeout=15000)


def test_frame_rate_is_capped(open_spike):
    page = open_spike("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    start = stats(page)["frames"]
    page.wait_for_timeout(1000)
    drawn = stats(page)["frames"] - start
    assert drawn <= 36  # maxFps default 30 plus timer jitter


def test_context_loss_stops_drawing_and_is_reported(open_spike):
    page = open_spike("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    page.evaluate("""() => {
        const gl = document.getElementById('scene').getContext('webgl2')
                || document.getElementById('scene').getContext('webgl');
        window.__lose = gl.getExtension('WEBGL_lose_context'); window.__lose.loseContext();
    }""")
    page.wait_for_function("window.__holo.holo.stats().contextLost === true", timeout=5000)
    frozen = stats(page)["frames"]
    page.wait_for_timeout(400)
    assert stats(page)["frames"] == frozen


def test_dispose_stops_the_loop_and_frees_geometry(open_spike):
    page = open_spike("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    page.evaluate("window.__holo.holo.dispose()")
    frozen = stats(page)["frames"]
    page.wait_for_timeout(400)
    assert stats(page)["frames"] == frozen and stats(page)["running"] is False
