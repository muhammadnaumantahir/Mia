"""MIA-HOLO-02/03: the hologram page and scene, served by the real app and run in real Chromium.

Chromium here has no GPU (software GL), so these prove behaviour: routing, responsive layout,
deterministic per-state poses, WebGL fallback, reduced motion, pausing while hidden, context loss,
disposal and that the committed page matches its sources. They say nothing about frame rate on the
owner's hardware (MIA-HOLO-06) and the page is a preview, not bound to live events (MIA-HOLO-04).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "frontend" / "holo"
PAGE = ROOT / "src" / "mia" / "observatory" / "hologram.html"
STATES = {"unknown", "idle", "listening", "thinking", "speaking", "interrupted", "tool_running",
          "awaiting_approval", "failed", "offline"}
NO_WEBGL = (
    "const g=HTMLCanvasElement.prototype.getContext;"
    "HTMLCanvasElement.prototype.getContext=function(t,...a){"
    "return /webgl/.test(t)?null:g.call(this,t,...a)};"
)


# ------------------------------------------------------------------ served page (no browser)


def test_route_serves_the_page_and_it_declares_itself_a_preview(tmp_path):
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3")))
    response = client.get("/hologram")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    body = response.text
    assert "Preview only: the state shown is the one you pick" in body  # the preview mode says so
    assert 'name="mode"' in body and "Live activity" in body
    assert 'role="status"' in body and 'aria-hidden="true"' in body  # canvas is decorative


def test_page_loads_nothing_from_the_network_except_the_runtime_contract():
    import re

    html = PAGE.read_text(encoding="utf-8")
    markup = html.split("<script>")[0]  # the hand-written part, before the bundled script
    assert "<script src" not in html and "<link" not in markup and "@import" not in markup
    assert not re.search(r"""(src|href)=["']https?:""", markup)
    assert "url(http" not in markup
    main_source = (SOURCES / "src" / "main.js").read_text(encoding="utf-8")
    assert re.findall(r"fetch\(\s*['\"]([^'\"]+)", main_source) == ["/api/runtime/contract"]
    assert "/api/runtime/contract" in html


def test_package_data_ships_the_page():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "hologram.html" in text


def test_committed_page_matches_its_sources():
    node = shutil.which("node")
    if not node or not shutil.which("npm"):
        pytest.skip("node/npm not available to rebuild the page")
    before = PAGE.read_bytes()
    try:
        if not (SOURCES / "node_modules").exists():
            subprocess.run(["npm", "ci"], cwd=SOURCES, check=True, capture_output=True, timeout=300)
        subprocess.run([node, "build.mjs"], cwd=SOURCES, check=True, capture_output=True, timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        PAGE.write_bytes(before)
        pytest.skip(f"could not rebuild the page: {exc}")
    after = PAGE.read_bytes()
    PAGE.write_bytes(before)  # never leave the working tree modified by a test
    assert after == before, "frontend/holo sources changed: run `npm run build` and commit hologram.html"


# ---------------------------------------------------------------------------- real browser


def _start(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    app = create_app(store=ObservatoryStore(tmp_path_factory.mktemp("holo") / "o.sqlite3"))
    server, thread, url = _start(app)
    yield url
    server.should_exit = True
    thread.join(timeout=5)


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
def open_page(browser, base):
    contexts = []

    def opener(query="", *, init_script=None, reduced_motion="no-preference", size=(1000, 800),
               live=False, context=None):
        if context is None:
            context = browser.new_context(reduced_motion=reduced_motion,
                                          viewport={"width": size[0], "height": size[1]})
            contexts.append(context)
        if init_script:
            context.add_init_script(init_script)
        page = context.new_page()
        if not live and "state=" not in query and "mode=" not in query:
            query = (query + "&" if query else "?") + "mode=preview"  # scene tests drive states by hand
        page.goto(f"{base}/hologram{query}")
        # The page re-applies its picker state once the runtime-contract fetch lands; let that
        # finish so tests that drive the renderer directly are not overwritten mid-test.
        page.wait_for_load_state("networkidle")
        return page

    yield opener
    for context in contexts:
        context.close()


def stats(page):
    return page.evaluate("window.__holo.holo.stats()")


def test_renders_and_animates_with_webgl(open_page):
    page = open_page("?state=thinking")
    assert page.evaluate("window.__holo.ok") is True
    page.wait_for_function("window.__holo.holo.stats().frames > 5", timeout=15000)
    s = stats(page)
    assert s["state"] == "thinking" and s["running"] and s["drawCalls"] >= 1
    assert page.locator("#fallback").is_hidden() and page.locator("#scene").is_visible()


def test_status_text_names_the_state_and_follows_the_selector(open_page):
    page = open_page("?state=listening")
    text = page.locator("#status").inner_text()
    assert text.startswith("listening:") and "listening session" in text
    page.select_option("#preview", "awaiting_approval")
    assert "approval" in page.locator("#status").inner_text()
    assert stats(page)["state"] == "awaiting_approval"


def test_statements_come_from_the_runtime_contract_when_available(open_page):
    page = open_page("?state=offline")
    page.wait_for_function("document.getElementById('status').textContent.includes('cannot be reached')")


def test_unknown_or_missing_state_shows_unknown_never_idle(open_page):
    for query in ("", "?state=mia_is_dreaming", "?state=constructor", "?state=__proto__"):
        page = open_page(query)
        assert stats(page)["state"] == "unknown"
        assert page.locator("#preview").input_value() == "unknown"


def test_renderer_itself_maps_an_unrecognised_state_to_unknown(open_page):
    # Bypasses the page's own normalisation: the renderer must hold the rule on its own.
    page = open_page("?state=thinking")
    for bad in ("mia_is_dreaming", "", "IDLE", "constructor"):
        page.evaluate("s => window.__holo.holo.setState(s)", bad)
        assert stats(page)["state"] == "unknown", bad


def test_every_state_has_a_distinct_look_not_colour_alone(open_page):
    page = open_page()
    assert set(page.evaluate("window.__holo.states")) == STATES
    looks = page.evaluate("""() => {
        const out = {};
        for (const s of window.__holo.states) {
            window.__holo.show(s);
            const h = window.__holo.holo; h.stop();
            h.step(0.5); const a = h.stats();
            out[s] = {state: a.state, shape: a.shape, scale: +a.scale.toFixed(4),
                      spin: +a.coreY.toFixed(4), ring: +a.ringZ.toFixed(4)};
            h.step(0.5);
        }
        return out;
    }""")
    assert set(looks) == STATES and all(v["state"] == s for s, v in looks.items())
    # Excluding colour, no two states may share both shape and motion signature.
    signatures = {(v["shape"], v["scale"], v["spin"], v["ring"]) for v in looks.values()}
    assert len(signatures) >= 9  # idle/unknown-style pairs may share a still pose, nothing else
    assert looks["failed"]["shape"] == looks["offline"]["shape"] == "angular"
    assert looks["thinking"]["shape"] == "sphere"


def test_pose_is_deterministic_and_matches_the_state_meaning(open_page):
    page = open_page()
    poses = page.evaluate("""() => {
        const L = {thinking: 0.9, offline: 0, failed: 0, awaiting_approval: 0};
        const out = {};
        for (const s of Object.keys(L)) {
            window.__holo.show(s); const h = window.__holo.holo; h.stop();
            out[s] = [];
        }
        for (const s of Object.keys(L)) {
            window.__holo.show(s); const h = window.__holo.holo; h.stop();
            const base = h.stats().t;
            h.step(0.1); h.step(0.1); const a = h.stats();
            out[s] = {dt: +(a.t - base).toFixed(3), spin: +a.coreY.toFixed(5), scale: +a.scale.toFixed(5)};
        }
        return out;
    }""")
    assert poses["thinking"]["spin"] > 0.0  # thinking turns
    assert poses["offline"]["spin"] == 0.0 and poses["failed"]["spin"] == 0.0  # dead states are still
    assert poses["offline"]["scale"] == 1.0
    assert poses["thinking"]["dt"] == 0.2


def test_a_long_stall_does_not_teleport_the_animation(open_page):
    page = open_page("?state=thinking")
    page.evaluate("window.__holo.holo.stop()")
    before = stats(page)["t"]
    page.evaluate("window.__holo.holo.step(60)")
    assert stats(page)["t"] - before == pytest.approx(0.25, abs=1e-6)


@pytest.mark.parametrize("size", [(360, 640), (768, 1024), (1440, 900)])
def test_layout_fits_every_width_without_horizontal_scroll(open_page, size):
    page = open_page("?state=idle", size=size)
    page.wait_for_function("window.__holo.holo.stats().frames > 1", timeout=15000)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0
    box = page.locator("#stage").bounding_box()
    assert box["width"] <= size[0] and box["height"] <= size[1] * 0.71
    canvas = page.locator("#scene").bounding_box()
    assert canvas["width"] > 100 and abs(canvas["width"] - canvas["height"]) < 2


def test_resize_keeps_the_scene_square_and_drawing(open_page):
    page = open_page("?state=idle", size=(1000, 800))
    page.wait_for_function("window.__holo.holo.stats().frames > 1", timeout=15000)
    page.set_viewport_size({"width": 400, "height": 700})
    page.wait_for_timeout(300)
    canvas = page.locator("#scene").bounding_box()
    assert canvas["width"] <= 400 and abs(canvas["width"] - canvas["height"]) < 2


def test_missing_webgl_shows_text_fallback_and_does_not_throw(open_page):
    page = open_page("?state=failed", init_script=NO_WEBGL)
    assert page.evaluate("window.__holo.ok") is False
    assert page.evaluate("window.__holo.reason") == "webgl_unavailable"
    assert page.locator("#fallback").is_visible() and page.locator("#scene").is_hidden()
    assert "unaffected" in page.locator("#fallback").inner_text()
    assert "failed" in page.locator("#status").inner_text()  # the state text still works


def test_reduced_motion_draws_static_frames_only(open_page):
    page = open_page("?state=thinking", reduced_motion="reduce")
    page.wait_for_timeout(600)
    before = stats(page)["frames"]
    page.wait_for_timeout(600)
    assert stats(page)["frames"] == before  # no animation loop
    page.evaluate("window.__holo.show('failed')")
    assert stats(page)["frames"] == before + 1  # a state change still redraws once


def test_loop_pauses_while_hidden_and_resumes(open_page):
    page = open_page("?state=listening")
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


def test_frame_rate_is_capped(open_page):
    page = open_page("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    start = stats(page)["frames"]
    page.wait_for_timeout(1000)
    assert stats(page)["frames"] - start <= 36  # default 30 fps plus timer jitter


def test_context_loss_stops_drawing_and_restore_resumes(open_page):
    page = open_page("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    page.evaluate("""() => {
        const c = document.getElementById('scene');
        const gl = c.getContext('webgl2') || c.getContext('webgl');
        window.__lose = gl.getExtension('WEBGL_lose_context'); window.__lose.loseContext();
    }""")
    page.wait_for_function("window.__holo.holo.stats().contextLost === true", timeout=5000)
    frozen = stats(page)["frames"]
    page.wait_for_timeout(400)
    assert stats(page)["frames"] == frozen
    page.evaluate("window.__lose.restoreContext()")
    page.wait_for_function("window.__holo.holo.stats().contextLost === false", timeout=5000)
    page.wait_for_function(f"window.__holo.holo.stats().frames > {frozen}", timeout=15000)


def test_dispose_stops_the_loop_and_frees_geometry(open_page):
    page = open_page("?state=idle")
    page.wait_for_function("window.__holo.holo.stats().frames > 2", timeout=15000)
    page.evaluate("window.__holo.holo.dispose()")
    frozen = stats(page)["frames"]
    page.wait_for_timeout(400)
    assert stats(page)["frames"] == frozen and stats(page)["running"] is False
