"""MIA-HOLO-06: performance budgets and graceful degradation for /hologram.

Budgets live in frontend/holo/budgets.json (one source; the page's governor reads the same file).
What this proves: the bundle and scene stay inside their budgets, the governor's policy behaves
(pure, no GPU needed), and the page really degrades and says so under load. What it cannot prove:
real-GPU frame times. Chromium here renders in software, so CPU cost numbers are indicative only
and are reported, not asserted tightly. Run ``PYTHONPATH=. python tests/test_holo_budget.py`` for a report.
"""

from __future__ import annotations

import gzip
import json

import pytest

from tests.test_holo_scene import PAGE, SOURCES, base, browser, open_page  # noqa: F401

pytest.importorskip("playwright.sync_api")

BUDGETS = json.loads((SOURCES / "budgets.json").read_text(encoding="utf-8"))
SLOW_DRAW = (
    "window.__load = true;"
    "for (const C of [WebGLRenderingContext, window.WebGL2RenderingContext]) { if (!C) continue;"
    " for (const name of ['drawElements', 'drawArrays']) { const orig = C.prototype[name];"
    "  C.prototype[name] = function (...a) { if (window.__load) { const t = performance.now();"
    "   while (performance.now() - t < 20); } return orig.apply(this, a); }; } }"
)


def gov(page, **options):
    """A fresh governor inside the page, driven by hand."""
    page.evaluate("(o) => { window.__g = window.__holoPerf.createGovernor(o); }", options)
    return lambda cost, interval=0.0: page.evaluate("([c, i]) => window.__g.record(c, i)", [cost, interval])


# ---------------------------------------------------------------- budgets file and bundle


def test_budget_levels_degrade_monotonically_and_level_zero_keeps_the_30fps_look():
    levels = BUDGETS["levels"]
    assert levels[0]["maxFps"] == 30 and levels[0]["pixelRatio"] <= 2
    assert [lv["maxFps"] for lv in levels] == sorted((lv["maxFps"] for lv in levels), reverse=True)
    assert [lv["pixelRatio"] for lv in levels] == sorted((lv["pixelRatio"] for lv in levels), reverse=True)
    assert len({lv["name"] for lv in levels}) == len(levels) and levels[-1]["maxFps"] >= 1


def test_bundle_stays_inside_its_budget():
    raw = PAGE.read_bytes()
    assert len(raw) <= BUDGETS["bundle"]["htmlBytes"], len(raw)
    assert len(gzip.compress(raw, 9)) <= BUDGETS["bundle"]["gzipBytes"]


def test_the_page_is_one_request_plus_the_contract(open_page):  # noqa: F811
    seen = []
    page = open_page("?view=text&mode=preview")  # opener already navigated; reload with a listener
    page.on("request", lambda r: seen.append(r.url.split("/hologram")[-1] or r.url))
    page.reload()
    page.wait_for_load_state("networkidle")
    paths = [u for u in seen if not u.startswith("data:")]
    assert all(u.startswith("?") or u.endswith("/api/runtime/contract") or u == "" for u in paths), paths


# ------------------------------------------------------------------- scene resource budget


def test_scene_stays_inside_its_resource_budget(open_page):  # noqa: F811
    page = open_page("?mode=preview&state=thinking")
    page.wait_for_timeout(300)
    s = page.evaluate("window.__holo.holo.stats()")
    scene = BUDGETS["scene"]
    assert s["drawCalls"] <= scene["drawCalls"]
    assert s["triangles"] <= scene["triangles"] and s["lines"] <= scene["lines"]
    assert s["geometries"] <= scene["geometries"] and s["textures"] <= scene["textures"]


def test_cycling_every_state_many_times_does_not_grow_resources(open_page):  # noqa: F811
    page = open_page("?mode=preview")
    before = page.evaluate("(() => { const s = window.__holo.holo.stats(); return [s.geometries, s.textures]; })()")
    page.evaluate("""() => { for (let i = 0; i < 300; i++)
      for (const s of window.__holo.states) window.__holo.show(s); }""")
    after = page.evaluate("(() => { const s = window.__holo.holo.stats(); return [s.geometries, s.textures]; })()")
    assert after[1] == 0 and after[0] <= BUDGETS["scene"]["geometries"]
    assert after[0] <= before[0] + 1  # the angular shape is created once, not once per switch


# ------------------------------------------------------------------ governor policy (pure)


def test_cheap_on_time_frames_never_degrade(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    assert all(rec(0.3, 33.0) is None for _ in range(2000))
    assert page.evaluate("window.__g.level()") == 0


def test_sustained_cost_steps_down_one_level_per_window_and_stops_at_the_last(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    levels = []
    for _ in range(3000):
        changed = rec(BUDGETS["frame"]["frameMs"] * 2, 0.0)
        if changed is not None:
            levels.append(changed)
    assert levels == [1, 2, 3]
    assert [c["why"] for c in page.evaluate("window.__g.changes()")] == ["cost"] * 3


def test_late_frames_degrade_even_when_cpu_cost_is_tiny(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    for _ in range(200):
        rec(0.2, 33.0 * 3)  # three times the 30 fps interval: the GPU is behind
    changes = page.evaluate("window.__g.changes()")
    assert changes and changes[0]["why"] == "late" and page.evaluate("window.__g.level()") >= 1


def test_one_spike_does_not_degrade(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    for i in range(500):
        rec(150.0 if i == 2 else 0.3, 33.0)  # early: before a full window exists
    assert page.evaluate("window.__g.changes()") == []  # not even a brief dip, which recovery would hide


def test_throttled_tab_gaps_are_not_load(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    for _ in range(500):
        rec(0.3, 5000.0)  # a hidden or throttled tab resuming
    assert page.evaluate("window.__g.level()") == 0


def test_recovery_is_slow_and_one_step_at_a_time(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page, startLevel=3)
    up = BUDGETS["frame"]["upAfterFrames"]
    window = BUDGETS["frame"]["window"]
    results = [rec(0.3, 1000 / 4) for _ in range(window + up - 5)]
    assert page.evaluate("window.__g.level()") == 3  # not yet: needs a full window plus a long calm run
    results += [rec(0.3, 1000 / 4) for _ in range(10)]
    assert page.evaluate("window.__g.level()") == 2
    assert [r for r in results if r is not None] == [2]


def test_a_noisy_load_near_the_budget_does_not_flap(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page)
    for i in range(6000):
        rec(BUDGETS["frame"]["frameMs"] * (0.7 if i % 2 else 0.9), 33.0)  # busy but inside budget
    assert page.evaluate("window.__g.changes().length") == 0
    for i in range(6000):
        rec(BUDGETS["frame"]["frameMs"] * (0.3 if (i // 40) % 2 else 1.6), 33.0)  # alternating load
    assert page.evaluate("window.__g.changes().length") <= 12  # bounded, not one change per window


def test_a_pinned_level_never_moves_until_released(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    rec = gov(page, pinLevel=1)
    for _ in range(500):
        assert rec(500.0, 500.0) is None
    assert page.evaluate("window.__g.level()") == 1 and page.evaluate("window.__g.pinned()") is True
    page.evaluate("window.__g.auto()")
    for _ in range(200):
        rec(500.0, 0.0)
    assert page.evaluate("window.__g.level()") > 1


def test_out_of_range_levels_are_clamped(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview")
    gov(page, pinLevel=99)
    assert page.evaluate("window.__g.level()") == len(BUDGETS["levels"]) - 1
    gov(page, startLevel=-4)
    assert page.evaluate("window.__g.level()") == 0


# ------------------------------------------------------------- the page applies the policy


def frames_in(page, ms):
    a = page.evaluate("window.__holo.holo.stats().frames")
    page.wait_for_timeout(ms)
    return page.evaluate("window.__holo.holo.stats().frames") - a


@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_each_pinned_level_applies_its_frame_rate_and_pixel_ratio(open_page, level):  # noqa: F811
    page = open_page(f"?mode=preview&state=thinking&quality={level}", size=(900, 800))
    spec = BUDGETS["levels"][level]
    s = page.evaluate("window.__holo.holo.stats()")
    assert s["level"] == level and s["levelName"] == spec["name"] and s["targetFps"] == spec["maxFps"]
    assert s["pixelRatio"] <= spec["pixelRatio"]
    n = frames_in(page, 2000)
    assert n <= spec["maxFps"] * 2 + 4  # never faster than the cap
    assert n >= max(1, spec["maxFps"] * 2 * 0.5)  # and actually running


def test_lower_levels_draw_fewer_frames_and_fewer_pixels(open_page):  # noqa: F811
    page = open_page("?mode=preview&state=thinking", size=(900, 800))
    counts, pixels = [], []
    for level in range(4):
        page.evaluate("(l) => window.__holo.holo.setQuality(l)", level)
        counts.append(frames_in(page, 1000))
        pixels.append(page.evaluate("document.getElementById('scene').width"))
    assert counts[0] > counts[1] > counts[3] and counts[2] >= counts[3]
    assert pixels[0] >= pixels[1] >= pixels[2] == pixels[3]


def test_reduced_detail_is_said_on_screen_and_the_state_stays_accurate(open_page):  # noqa: F811
    page = open_page("?mode=preview&state=failed&quality=0")
    assert not page.is_visible("#perfNote")
    page = open_page("?mode=preview&state=failed&quality=3")
    assert page.is_visible("#perfNote") and "slowed" in page.inner_text("#perfNote")
    assert "failed" in page.inner_text("#status") and page.evaluate("window.__holo.current()") == "failed"


def test_a_slow_device_is_degraded_automatically_and_told_so(open_page):  # noqa: F811
    page = open_page("?mode=preview&state=thinking", init_script=SLOW_DRAW)
    page.wait_for_function("window.__holo.holo.stats().level >= 1", timeout=20000)
    assert page.is_visible("#perfNote")
    assert page.evaluate("window.__holo.holo.governor.changes()[0].why") in ("cost", "late")
    page.wait_for_function("window.__holo.holo.stats().level === 3", timeout=40000)
    assert page.evaluate("window.__holo.holo.stats().targetFps") == BUDGETS["levels"][3]["maxFps"]
    assert "failed" not in page.inner_text("#status") and "thinking" in page.inner_text("#status")


def test_quality_can_be_handed_back_to_the_governor(open_page):  # noqa: F811
    page = open_page("?mode=preview&quality=2")
    assert page.evaluate("window.__holo.holo.governor.pinned()") is True
    page.evaluate("window.__holo.holo.setQuality(null)")
    assert page.evaluate("window.__holo.holo.governor.pinned()") is False


def test_nothing_is_drawn_in_the_text_view_while_hidden_or_with_reduced_motion(open_page):  # noqa: F811
    page = open_page("?mode=preview&view=text")
    assert frames_in(page, 600) == BUDGETS["idle"]["framesWhileHiddenOrTextView"]
    page = open_page("?mode=preview", reduced_motion="reduce")
    assert frames_in(page, 600) <= 1


def test_an_invalid_quality_parameter_is_ignored(open_page):  # noqa: F811
    for bad in ("9", "-1", "abc", "1.5", ""):
        page = open_page(f"?mode=preview&quality={bad}")
        assert page.evaluate("window.__holo.holo.governor.pinned()") is False, bad


# ------------------------------------------------------------------------------ report


def measure():  # pragma: no cover - manual profiling aid
    import socket
    import tempfile
    import threading
    import time
    from pathlib import Path

    import uvicorn
    from playwright.sync_api import sync_playwright

    from mia.observatory.api import create_app
    from mia.observatory.store import ObservatoryStore

    app = create_app(store=ObservatoryStore(Path(tempfile.mkdtemp()) / "o.sqlite3"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(1.5)
    raw = PAGE.read_bytes()
    report = {"html_bytes": len(raw), "gzip_bytes": len(gzip.compress(raw, 9)), "levels": []}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        for level in range(4):
            pg = b.new_page(viewport={"width": 1000, "height": 800}, device_scale_factor=2)
            pg.goto(f"http://127.0.0.1:{port}/hologram?mode=preview&state=thinking&quality={level}")
            pg.wait_for_load_state("networkidle")
            a = pg.evaluate("window.__holo.holo.stats().frames")
            pg.wait_for_timeout(3000)
            s = pg.evaluate("window.__holo.holo.stats()")
            report["levels"].append({"level": level, "fps_measured": round((s["frames"] - a) / 3, 1),
                                     "avg_cpu_ms": round(s["avgCostMs"], 2), "canvas_px": pg.evaluate(
                                         "document.getElementById('scene').width")})
    server.should_exit = True
    return report


def test_pixel_ratio_steps_down_on_a_high_dpi_screen(browser, base):  # noqa: F811
    context = browser.new_context(device_scale_factor=2, viewport={"width": 900, "height": 800})
    try:
        widths = {}
        for level in range(4):
            page = context.new_page()
            page.goto(f"{base}/hologram?mode=preview&state=idle&quality={level}")
            page.wait_for_load_state("networkidle")
            css = page.evaluate("document.getElementById('scene').getBoundingClientRect().width")
            widths[level] = page.evaluate("document.getElementById('scene').width") / css
            page.close()
        assert widths[0] == pytest.approx(2.0, abs=0.05)
        assert widths[1] == pytest.approx(1.25, abs=0.05)
        assert widths[2] == pytest.approx(1.0, abs=0.05) and widths[3] == pytest.approx(1.0, abs=0.05)
    finally:
        context.close()


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(measure(), indent=2))
