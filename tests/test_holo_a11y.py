"""MIA-HOLO-05: the hologram page is usable without 3D, without motion and without sight of colour.

Real Chromium against the real app. Checks behaviour and arithmetic (contrast ratios are computed
from the rendered colours), not a screen reader: no assistive technology is run here, so the
labels are verified as text and structure only.
"""

from __future__ import annotations

import pytest

from tests.test_holo_scene import NO_WEBGL, STATES, base, browser, open_page  # noqa: F401

pytest.importorskip("playwright.sync_api")

LUMINANCE_JS = """
(sel) => {
  const el = document.querySelector(sel);
  const parse = (c) => c.match(/[\\d.]+/g).map(Number);
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  let bg = el, bgc;
  while (bg) { bgc = parse(getComputedStyle(bg).backgroundColor); if (bgc.length < 4 || bgc[3] > 0.99) break; bg = bg.parentElement; }
  const fg = parse(getComputedStyle(el).color);
  const a = lum(fg), b = lum(bgc);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}
"""


def ratio(page, selector):
    return page.evaluate(LUMINANCE_JS, selector)


def test_text_view_shows_every_state_with_a_distinct_shape(open_page):  # noqa: F811
    page = open_page("?view=text")
    seen = {}
    for state in sorted(STATES):
        page.evaluate("(s) => window.__holo.show(s)", state)
        assert page.is_visible("#glyphView") and not page.is_visible("#scene")
        assert page.inner_text("#glyphName") == state.replace("_", " ")
        assert state.replace("_", " ") in page.inner_text("#status")
        seen[state] = page.inner_html("#glyph")
    assert len(set(seen.values())) == len(STATES)  # no two states share an outline


def test_text_view_does_not_depend_on_colour(open_page):  # noqa: F811
    page = open_page("?view=text")
    colours = {
        page.evaluate("(s) => { window.__holo.show(s); return getComputedStyle(document.getElementById('glyph')).color; }", s)
        for s in STATES
    }
    assert len(colours) == 1  # identical colour for all ten: the shape and the words carry the meaning


def test_text_view_stops_the_3d_loop_and_switching_back_restarts_it(open_page):  # noqa: F811
    page = open_page("")
    assert page.evaluate("window.__holo.holo.stats().running") is True
    page.check('input[name=view][value="text"]')
    assert page.evaluate("window.__holo.holo.stats().running") is False
    page.check('input[name=view][value="3d"]')
    page.wait_for_timeout(200)
    assert page.evaluate("window.__holo.holo.stats().running") is True
    assert page.is_visible("#scene") and not page.is_visible("#glyphView")


def test_no_webgl_falls_back_to_the_text_view_with_the_state(open_page):  # noqa: F811
    page = open_page("?state=failed", init_script=NO_WEBGL)
    assert page.is_visible("#glyphView") and page.is_visible("#fallback")
    assert page.inner_text("#glyphName") == "failed"
    assert "failed" in page.inner_text("#status")
    assert page.evaluate("window.__holoView.view()") == "text"


def test_status_is_a_polite_live_region_and_not_rewritten_when_unchanged(open_page):  # noqa: F811
    page = open_page("")
    assert page.get_attribute("#status", "role") == "status"
    assert page.get_attribute("#status", "aria-live") == "polite"
    assert page.get_attribute("#evidence", "aria-live") == "off"  # the "n s ago" line never chatters
    page.evaluate("""() => { window.__writes = 0;
      new MutationObserver((m) => { window.__writes += m.length; })
        .observe(document.getElementById('status'), {childList: true, characterData: true, subtree: true});
      window.__holo.show('idle'); }""")
    page.wait_for_timeout(100)
    before = page.evaluate("window.__writes")
    page.evaluate("() => { for (let i = 0; i < 5; i++) window.__holo.show('idle'); }")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__writes") == before  # same state again: nothing to announce


def test_decorative_graphics_are_hidden_from_assistive_technology(open_page):  # noqa: F811
    page = open_page("")
    assert page.get_attribute("#scene", "aria-hidden") == "true"
    assert page.get_attribute("#glyph", "aria-hidden") == "true"
    assert page.get_attribute("#glyph", "focusable") == "false"


def test_every_control_has_an_accessible_name_and_the_page_has_one_h1(open_page):  # noqa: F811
    page = open_page("")
    assert page.locator("h1").count() == 1 and page.get_attribute("html", "lang") == "en"
    names = page.evaluate("""() => [...document.querySelectorAll('input,select,button')].map((el) => {
      const lab = el.labels && el.labels[0] ? el.labels[0].textContent.trim() : '';
      return lab || el.getAttribute('aria-label') || '';
    })""")
    assert names and all(names), names
    assert page.locator("fieldset > legend").count() == 2


def test_keyboard_reaches_every_control_in_a_sensible_order_with_a_visible_focus(open_page):  # noqa: F811
    page = open_page("")
    order = []
    for _ in range(4):  # skip link, view group, show group, preview picker
        page.keyboard.press("Tab")
        order.append(page.evaluate("""() => { const a = document.activeElement;
          const cs = getComputedStyle(a);
          return [a.tagName + (a.name ? ':' + a.name : '') + (a.id ? '#' + a.id : ''),
                  cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2]; }"""))
    tags = [o[0] for o in order]
    assert tags[0].startswith("A"), tags  # skip link first
    assert any(t.startswith("INPUT:view") for t in tags) and any(t.startswith("INPUT:mode") for t in tags)
    assert all(o[1] for o in order), order  # every stop has a clearly visible focus ring


def test_skip_link_moves_focus_target_to_the_status(open_page):  # noqa: F811
    page = open_page("")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#status"


def test_radio_groups_work_with_arrow_keys(open_page):  # noqa: F811
    page = open_page("")
    page.focus("input[name=view]:checked")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("window.__holoView.view()") == "text"
    page.keyboard.press("ArrowUp")
    assert page.evaluate("window.__holoView.view()") == "3d"


def test_the_preview_picker_is_keyboard_operable_and_disabled_in_live_mode(open_page):  # noqa: F811
    page = open_page("?mode=preview")
    assert page.is_enabled("#preview")
    page.focus("#preview")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("window.__holo.current()") == page.input_value("#preview")
    page.check('input[name=mode][value="live"]')
    assert page.is_disabled("#preview")


@pytest.mark.parametrize("selector", ["h1", "#status", "#evidence", "#previewNote", "legend",
                                      "label.inline", "#glyphName", "select"])
def test_text_meets_wcag_aa_contrast(open_page, selector):  # noqa: F811
    page = open_page("?mode=preview&view=text")
    page.evaluate("document.getElementById('previewNote').hidden = false")
    assert ratio(page, selector) >= 4.5, selector


def test_focus_ring_has_enough_contrast_against_the_page(open_page):  # noqa: F811
    page = open_page("")
    page.keyboard.press("Tab")
    ring = page.evaluate("""() => { const parse = (c) => c.match(/[\\d.]+/g).map(Number);
      const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
      const a = lum(parse(getComputedStyle(document.activeElement).outlineColor));
      const b = lum(parse(getComputedStyle(document.body).backgroundColor));
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05); }""")
    assert ring >= 3.0


def test_controls_are_big_enough_to_tap(open_page):  # noqa: F811
    page = open_page("")
    sizes = page.evaluate("""() => [...document.querySelectorAll('label.inline,select')].map((el) => {
      const r = el.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })""")
    assert sizes and all(w >= 24 and h >= 24 for w, h in sizes)  # WCAG 2.2 minimum
    assert all(h >= 40 for _, h in sizes)  # and comfortably tappable


@pytest.mark.parametrize("size", [(320, 640), (360, 740), (200, 600)])
def test_reflows_at_narrow_widths_with_nothing_cut_off(open_page, size):  # noqa: F811
    page = open_page("?view=text&mode=preview", size=size)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    for selector in ("#glyphView", "#status", "fieldset", "#preview"):
        box = page.locator(selector).first.bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= size[0] + 1, selector


def test_text_scales_with_zoom_without_clipping(open_page):  # noqa: F811
    page = open_page("?view=text&mode=preview", size=(640, 800))
    page.evaluate("document.documentElement.style.fontSize = '32px'")  # 200% text size
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.is_visible("#status") and page.is_visible("#glyphName")


def test_reduced_motion_keeps_state_distinct_without_animation(open_page):  # noqa: F811
    page = open_page("?mode=preview", reduced_motion="reduce")
    page.evaluate("window.__holo.show('thinking')")
    a = page.evaluate("window.__holo.holo.stats().frames")
    page.wait_for_timeout(400)
    b = page.evaluate("window.__holo.holo.stats().frames")
    assert b - a <= 2  # still frames, not a render loop
    assert "thinking" in page.inner_text("#status")


def test_forced_colors_keep_borders_and_the_glyph_visible(browser, base):  # noqa: F811
    context = browser.new_context(forced_colors="active", viewport={"width": 800, "height": 700})
    try:
        page = context.new_page()
        page.goto(f"{base}/hologram?view=text&mode=preview&state=failed")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("getComputedStyle(document.getElementById('stage')).borderTopColor") != \
            page.evaluate("getComputedStyle(document.getElementById('stage')).backgroundColor")
        assert page.is_visible("#glyph") and page.inner_text("#glyphName") == "failed"
    finally:
        context.close()


def test_live_state_changes_update_the_text_view_too(open_page):  # noqa: F811
    page = open_page("?view=text", live=True)
    page.evaluate("window.__holoLive.tracker.accept({v:1,sender:'t',type:'set',kind:'chat_turn_started',detail:{}})")
    page.evaluate("window.__holoLive.binding.nudge(0)")
    page.wait_for_function("document.getElementById('glyphName').textContent === 'thinking'", timeout=8000)
    assert "thinking" in page.inner_text("#status")
