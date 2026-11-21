"""MIA-UX-06: read-only "Quality controls" panel on the AI Gateway dashboard.

* The served page is well formed (regression: ~6 KB of duplicated script used to trail ``</html>``
  and render as stray text).
* The panel is read-only (no controls, no write requests).
* The page's real render functions are run under Node against the *real* payloads of
  ``/api/brain/judges`` and ``/api/gateway/escalation`` from a live app, in default and enabled
  configurations, plus endpoint-failure and hostile-string cases. JS logic only; not a real browser.
"""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "gateway" / "dashboard.html"

ENV = (
    "MIA_JUDGES_ENABLED", "MIA_JUDGES_MAX", "MIA_JUDGES_ALLOW_REMOTE", "MIA_JUDGES_ALLOW_PAID",
    "MIA_ESCALATION_ENABLED", "MIA_ESCALATION_ALLOW_PAID", "MIA_ESCALATION_MAX_COST_USD",
    "MIA_ESCALATION_MAX_ATTEMPTS",
)


def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def make_client(tmp_path, monkeypatch, **env) -> TestClient:
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return TestClient(create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3")))


class _TailText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.visible: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if data.strip() and not {"script", "style"} & set(self.stack):
            self.visible.append(data.strip())


def test_served_dashboard_has_one_document_end_and_no_stray_trailing_text(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    page = client.get("/gateway")
    assert page.status_code == 200
    html = page.text
    assert html.count("</html>") == 1 and html.count("<script>") == html.count("</script>") == 1
    assert html[html.index("</html>") + len("</html>"):].strip() == ""
    assert "Quality controls" in html and 'id="quality"' in html
    # nothing outside <script>/<style> may follow the document end
    parser = _TailText()
    parser.feed(html[html.index("</html>"):])
    assert parser.visible == []


def test_quality_panel_is_strictly_read_only():
    html = source()
    section = html[html.index('aria-labelledby="quality-title"'): html.index("</section>", html.index('id="quality"'))]
    assert not re.search(r"<(button|input|select|textarea|form)\b", section)
    js = html[html.index("const SECONDS="): html.index("async function updateModel(")]
    assert "method" not in js and "POST" not in js and "innerHTML" not in js
    assert "/api/brain/judges" in js and "/api/gateway/escalation" in js
    assert "loadQuality();" in html[html.index("async function load(){"):][:200]


HARNESS = r"""
const assert = require('assert');
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.textContent = ''; this.children = []; }
  append(...kids) { for (const k of kids) this.children.push(k); }
  prepend(...kids) { this.children.unshift(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
}
global.document = {createElement: (t) => new El(t)};
const els = {quality: new El('div')};
const $ = (id) => els[id];
__SOURCE__
const walk = (e) => [e.textContent, ...e.children.flatMap(walk)];
function summarize(host) {
  return host.children.map(card => {
    const head = card.children[0];
    const kv = card.children.find(c => c.tag === 'dl');
    const pairs = {};
    if (kv) for (let i = 0; i < kv.children.length; i += 2) pairs[walk(kv.children[i]).join('')] = walk(kv.children[i + 1]).join('|');
    return {title: head.children[0].textContent, state: head.children[1].textContent, stateClass: head.children[1].className,
            pairs, notes: card.children.filter(c => c.className === 'qnote').map(c => c.textContent)};
  });
}
const input = JSON.parse(process.argv[2]);
(async () => {
  const out = {};
  for (const [name, c] of Object.entries(input.cases)) {
    renderQuality($('quality'), c.judges, c.escalation);
    out[name] = summarize($('quality'));
  }
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e.stack || String(e)); process.exit(1); });
"""


def run_cases(tmp_path, cases):
    html = source()
    start = html.index("const money=")
    helpers = html[start: html.index("\n", html.index("function node(tag,cls,text)"))]
    code = helpers + "\n" + html[html.index("const SECONDS="): html.index("async function fetchQuality(")]
    script = tmp_path / "quality.js"
    script.write_text(HARNESS.replace("__SOURCE__", code), encoding="utf-8")
    run = subprocess.run(["node", str(script), json.dumps({"cases": cases})],
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    return json.loads(run.stdout.strip().splitlines()[-1])


def live(client):
    return {"ok": True, "data": client.get("/api/brain/judges").json()}, \
           {"ok": True, "data": client.get("/api/gateway/escalation").json()}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_panel_renders_real_api_payloads_truthfully_by_default_and_when_enabled(tmp_path, monkeypatch):
    # The policy is read from the environment per request, so fetch each payload while its own
    # configuration is active.
    j0, e0 = live(make_client(tmp_path / "a", monkeypatch))
    enabled = make_client(
        tmp_path / "b", monkeypatch,
        MIA_JUDGES_ENABLED="true", MIA_ESCALATION_ENABLED="true",
        MIA_ESCALATION_ALLOW_PAID="true", MIA_ESCALATION_MAX_COST_USD="0",
    )
    j1, e1 = live(enabled)
    out = run_cases(tmp_path, {
        "default": {"judges": j0, "escalation": e0},
        "enabled": {"judges": j1, "escalation": e1},
        "judges_down": {"judges": {"ok": False, "message": "Judges status failed: HTTP 500"}, "escalation": e1},
        "hostile": {"judges": {"ok": True, "data": {
            "policy": {"enabled": True, "authority": "<img src=x onerror=alert(1)>"},
            "stats": {"top_skip_reasons": {"<b>x</b>": 3}}}}, "escalation": e0},
    })

    judges0, esc0 = out["default"]
    assert judges0["title"] == "Advisory judges" and judges0["state"] == "Off (default)"
    assert judges0["stateClass"].endswith("status-warn")
    assert judges0["pairs"]["Remote judges"] == "Blocked" and judges0["pairs"]["Paid judges"] == "Blocked"
    assert judges0["pairs"]["Judges per answer"] == "2 (hard cap 3)"
    assert judges0["pairs"]["Ran / lowered confidence"] == "0 / 0"
    assert any("advisory only" in n for n in judges0["notes"])  # server-provided authority wording
    assert esc0["state"] == "Off (default)" and esc0["pairs"]["Paid escalation"] == "Blocked"
    assert esc0["pairs"]["Streaming replies"] == "Not escalated"
    assert "low_confidence" in esc0["pairs"]["Triggers"]
    assert any("process-local" in n for n in esc0["notes"])

    judges1, esc1 = out["enabled"]
    assert judges1["state"] == "On (advisory)" and judges1["stateClass"].endswith("status-good")
    assert esc1["state"] == "On"
    # paid was requested but the cost cap is 0, so it must NOT read as effective
    assert e1["data"]["policy"]["paid_escalation_effective"] is False
    assert esc1["pairs"]["Paid escalation"].startswith("Requested but not effective")

    down_judges, down_esc = out["judges_down"]
    assert down_judges["state"] == "Unavailable" and "HTTP 500" in down_judges["notes"][0]
    assert down_esc["state"] == "On"  # the other card still renders

    hostile = out["hostile"][0]
    assert hostile["notes"][0] == "<img src=x onerror=alert(1)>"  # rendered as text, never as markup
    assert hostile["pairs"]["Top skip reasons"] == "<b>x</b>: 3"
