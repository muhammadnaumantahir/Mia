"""MIA-LIFE-12: usage, spend, latency, CPU/RAM, storage and workload dashboard.

* Aggregation: real ``UsageLedger`` rows, injected psutil/disk fakes; each section degrades
  on its own and unknown values stay ``None`` (never zero).
* API: ``/api/usage/dashboard`` on a live app: shape, read-only, no machine paths or secrets.
* UI: the page's real render functions run under Node against the live payload and hostile or
  unknown-valued fixtures. JS logic only; not a real browser.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mia.gateway.ledger import UsageLedger
from mia.observatory import usage_dashboard as ud
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.observatory.usage_dashboard import build_usage_dashboard

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "gateway" / "dashboard.html"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
Disk = namedtuple("Disk", "total used free")


class FakeMonitor:
    def __init__(self, fail=False):
        self.fail = fail

    def snapshot(self):
        if self.fail:
            raise RuntimeError('Install Mia with the "computer" extra at /secret/path')
        return {"cpu_percent": 12.5, "memory_percent": 40.0, "memory_available_bytes": 6 * 2**30,
                "memory_total_bytes": 16 * 2**30, "process_count": 210}


class FakePsutil:
    class _Proc:
        def cpu_times(self):
            return namedtuple("T", "user system")(3.25, 1.0)

        def memory_info(self):
            return namedtuple("M", "rss")(150 * 2**20)

        def num_threads(self):
            return 9

    def Process(self):  # noqa: N802 - mirrors psutil
        return self._Proc()


def disk(_path):
    return Disk(500 * 2**30, 200 * 2**30, 300 * 2**30)


def record(ledger, *, free=True, cost=None, total_ms=400.0, first=100.0, tokens=(10, 20), model="m"):
    ledger.record(provider_id="p", model_id=model, task_type="chat", input_tokens=tokens[0],
                  output_tokens=tokens[1], estimated_input_tokens=10, estimated_output_tokens=20,
                  cost_usd=cost, free_tier=free, data_local=free, first_token_ms=first,
                  total_ms=total_ms)


def build(tmp_path, **over):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    if "ledger" not in over:
        over["ledger"] = UsageLedger(data / "u.sqlite3")
    kwargs = dict(resource_monitor=FakeMonitor(),
                  data_dir=data, psutil_module=FakePsutil(), disk_usage=disk, now=NOW,
                  workload_probes={"a": lambda: {"enabled": True, "running": False}})
    kwargs.update(over)
    return build_usage_dashboard(**kwargs)


def test_all_sections_report_source_time_and_real_values(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_MAX_DAILY_SPEND_USD", "2.5")
    monkeypatch.delenv("MIA_ALLOW_PAID", raising=False)
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    for ms in (100.0, 200.0, 300.0):
        record(ledger, total_ms=ms)
    record(ledger, free=False, cost=0.25, model="paid")
    d = build(tmp_path, ledger=ledger, now=datetime.now(UTC))
    for name in ("usage", "resources", "storage", "workload"):
        assert d[name]["status"] == "ok" and d[name]["source"] and d[name]["measured_at"]
    u = d["usage"]
    assert u["requests"] == 4 and u["paid_requests"] == 1 and u["cost_usd"] == 0.25
    assert u["latency_ms"]["total"]["count"] == 4 and u["latency_ms"]["total"]["p95"] == 400.0
    assert {m["model_id"] for m in u["by_model"]} == {"m", "paid"}
    assert u["budget"]["allow_paid"] is False
    assert u["budget"]["limits"]["daily"] == {"configured": True, "valid": True, "limit_usd": 2.5}
    assert u["budget"]["limits"]["monthly"]["configured"] is False
    r = d["resources"]
    assert r["cpu_percent"] == 12.5 and r["mia_process"]["memory_rss_bytes"] == 150 * 2**20
    assert r["mia_process"]["cpu_seconds_total"] == 4.25 and r["mia_process"]["threads"] == 9
    s = d["storage"]
    assert s["disk_free_bytes"] == 300 * 2**30 and s["data_dir_truncated"] is False


def test_unknown_values_stay_unknown_not_zero(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    # The ledger's own write path refuses unknown-cost paid rows, so such a row can only come
    # from legacy or externally written data: insert one directly.
    with sqlite3.connect(ledger.database_path) as db:
        db.execute(
            "INSERT INTO usage (recorded_at, provider_id, model_id, task_type, input_tokens, "
            "output_tokens, token_source, cost_usd, free_tier, data_local, streamed) "
            "VALUES (?, 'p', 'm', 'chat', 5, 5, 'estimated', NULL, 0, 0, 0)",
            (datetime.now(UTC).isoformat(),),
        )
    u = build(tmp_path, ledger=ledger, now=datetime.now(UTC))["usage"]
    assert u["cost_usd"] is None and u["spend"]["today_utc_usd"] is None
    assert u["spend"]["month_utc_usd"] is None
    assert u["latency_ms"]["total"] == {"count": 0, "mean": None, "median": None, "p95": None}
    assert any("unknown, not zero" in n for n in u["notes"])
    assert u["by_model"][0]["cost_usd"] is None


def test_empty_ledger_is_ok_with_unmeasured_latency(tmp_path):
    u = build(tmp_path, now=datetime.now(UTC))["usage"]
    assert u["status"] == "ok" and u["requests"] == 0
    assert u["latency_ms"]["total"]["median"] is None and u["by_model"] == []


def test_each_section_degrades_independently_without_leaking_error_text(tmp_path):
    class Broken:
        def window_summary(self, *a):
            raise RuntimeError(f"disk image malformed at {tmp_path}")

    def boom():
        raise RuntimeError("token=sk-secret")

    d = build(tmp_path, ledger=Broken(), resource_monitor=FakeMonitor(fail=True),
              data_dir=tmp_path / "missing", workload_probes={"x": boom, "y": lambda: {"enabled": False}})
    assert [d[k]["status"] for k in ("usage", "resources", "storage")] == ["unavailable"] * 3
    assert d["workload"]["status"] == "ok"
    by_name = {i["name"]: i for i in d["workload"]["items"]}
    assert by_name["x"]["status"] == "unavailable" and by_name["y"]["status"] == "ok"
    dumped = json.dumps(d)
    for leak in (str(tmp_path), "sk-secret", "/secret/path", "malformed"):
        assert leak not in dumped
    assert all(d[k]["reason"] for k in ("usage", "resources", "storage"))


def test_missing_collaborators_are_unavailable_not_errors(tmp_path):
    d = build(tmp_path, ledger=None, resource_monitor=None, data_dir=None, workload_probes=None)
    assert [d[k]["status"] for k in ("usage", "resources", "storage", "workload")] == ["unavailable"] * 4


def test_process_stats_missing_keep_host_stats(tmp_path):
    class NoProc:
        def Process(self):  # noqa: N802
            raise OSError("denied")

    r = build(tmp_path, psutil_module=NoProc())["resources"]
    assert r["status"] == "ok" and r["cpu_percent"] == 12.5
    assert r["mia_process"]["measured"] is False


def test_storage_is_bounded_symlink_safe_and_path_free(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "sub").mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x" * 10_000)
    (data / "big.sqlite3").write_bytes(b"x" * 5000)
    (data / "sub" / "small.db").write_bytes(b"x" * 100)
    try:
        (data / "link.bin").symlink_to(outside)
    except OSError:
        pass
    s = build(tmp_path, ledger=None)["storage"]
    assert s["data_dir_bytes"] == 5100 and s["data_dir_files"] == 2  # link and outside excluded
    assert s["largest_files"][0] == {"name": "big.sqlite3", "bytes": 5000}
    assert s["largest_files"][1]["name"] == os.path.join("sub", "small.db")
    monkeypatch.setattr(ud, "MAX_FILES_WALKED", 1)
    capped = build(tmp_path, ledger=None)["storage"]
    assert capped["data_dir_truncated"] is True and capped["data_dir_files"] == 1


def test_days_are_clamped(tmp_path):
    assert build(tmp_path, days=0)["window_days"] == 1
    assert build(tmp_path, days=10_000)["window_days"] == 365


# ---- live API ----------------------------------------------------------------------------

def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-for-output")
    return TestClient(create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3")))


def test_api_returns_all_sections_is_read_only_and_leaks_nothing(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    res = client.get("/api/usage/dashboard")
    assert res.status_code == 200
    d = res.json()
    assert set(d) == {"generated_at", "window_days", "usage", "resources", "storage", "workload"}
    assert {i["name"] for i in d["workload"]["items"]} == {"autonomous_life", "experiment_snapshots"}
    assert d["usage"]["status"] == "ok" and d["usage"]["requests"] == 0
    dumped = res.text
    assert str(tmp_path) not in dumped and "sk-test-not-for-output" not in dumped
    for method in (client.post, client.put, client.delete):
        assert method("/api/usage/dashboard").status_code == 405
    assert client.get("/api/usage/dashboard?days=9999").json()["window_days"] == 365


# ---- UI (Node) ---------------------------------------------------------------------------

def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_usage_panel_is_read_only_and_uses_textcontent_only():
    html = source()
    section = html[html.index('aria-labelledby="usage-title"'): html.index("</section>", html.index('id="usage-dash"'))]
    assert not re.search(r"<(button|input|select|textarea|form)\b", section)
    js = html[html.index("const known="): html.index("async function updateModel(")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "POST", "method", "eval("):
        assert banned not in js
    assert "/api/usage/dashboard" in js
    assert "loadUsageDash();" in html[html.index("async function load(){"):][:200]


HARNESS = r"""
class El {
  constructor(tag) { this.tag = tag; this.className = ''; this.textContent = ''; this.children = []; }
  append(...kids) { for (const k of kids) this.children.push(k); }
  replaceChildren(...kids) { this.children = [...kids]; }
}
global.document = {createElement: (t) => new El(t)};
const els = {'usage-dash': new El('div')};
const $ = (id) => els[id];
__SOURCE__
const walk = (e) => [e.textContent, ...e.children.flatMap(walk)];
function summarize(host) {
  return host.children.map(card => {
    const head = card.children[0];
    const kv = card.children.find(c => c.tag === 'dl');
    const pairs = {};
    if (kv) for (let i = 0; i < kv.children.length; i += 2) pairs[walk(kv.children[i]).join('')] = walk(kv.children[i + 1]).join('|');
    return {title: head.children[0].textContent, state: head.children[1].textContent, pairs,
            notes: card.children.filter(c => c.className === 'qnote').map(c => c.textContent)};
  });
}
const input = JSON.parse(process.argv[2]);
const out = {};
for (const [name, result] of Object.entries(input)) {
  renderUsageDash($('usage-dash'), result);
  out[name] = summarize($('usage-dash'));
}
console.log(JSON.stringify(out));
"""


def run_ui(tmp_path, cases):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    html = source()
    start = html.index("const money=")
    helpers = html[start: html.index("\n", html.index("function node(tag,cls,text)"))]
    quality = html[html.index("const SECONDS="): html.index("function judgesCard(")]
    usage = html[html.index("const known="): html.index("async function loadUsageDash(")]
    script = tmp_path / "usage.js"
    script.write_text(HARNESS.replace("__SOURCE__", helpers + "\n" + quality + "\n" + usage), encoding="utf-8")
    run = subprocess.run([node, str(script), json.dumps(cases)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    return json.loads(run.stdout.strip().splitlines()[-1])


def titles(cards):
    return [c["title"] for c in cards]


def test_ui_renders_live_payload_with_all_five_cards(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    live = {"ok": True, "data": client.get("/api/usage/dashboard").json()}
    cards = run_ui(tmp_path, {"live": live})["live"]
    assert titles(cards) == ["Inference usage", "Spend and budgets", "CPU and memory", "Storage",
                             "Background workload"]
    assert cards[0]["pairs"]["Requests"] == "0"  # a measured, genuine zero
    assert cards[0]["pairs"]["Total latency"] == "Not measured"  # no samples: not "0 ms"
    assert all(c["state"] in ("Measured", "Unavailable") for c in cards)
    assert any("Source:" in n for n in cards[0]["notes"])
    assert "enabled" in json.dumps(cards[4]["pairs"]) or "disabled" in json.dumps(cards[4]["pairs"])


def test_ui_shows_unknown_for_null_values_never_zero(tmp_path):
    data = {
        "usage": {"status": "ok", "source": "ledger", "measured_at": "t", "window_days": 30,
                  "requests": 3, "paid_requests": 1, "non_local_requests": 0, "input_tokens": None,
                  "output_tokens": None, "cost_usd": None,
                  "spend": {"today_utc_usd": None, "month_utc_usd": None},
                  "latency_ms": {"total": {"count": 0, "mean": None, "median": None, "p95": None},
                                 "first_token": None},
                  "by_model": [{"provider_id": "p", "model_id": "m", "requests": 3, "cost_usd": None,
                                "avg_total_ms": None, "data_local": False}],
                  "budget": {"allow_paid": False, "limits": {
                      "per_request": {"configured": False, "valid": False, "limit_usd": None},
                      "daily": {"configured": True, "valid": False, "limit_usd": None},
                      "monthly": {"configured": True, "valid": True, "limit_usd": 5.0}}},
                  "reservations": {"active_count": 0, "active_reserved_usd": 0.0},
                  "notes": ["1 paid request(s) have no known price; their cost is unknown, not zero."]},
        "resources": {"status": "ok", "source": "psutil", "measured_at": "t", "cpu_percent": None,
                      "memory_percent": None, "memory_available_bytes": None,
                      "memory_total_bytes": None, "process_count": None,
                      "mia_process": {"measured": False}},
        "storage": {"status": "ok", "source": "fs", "measured_at": "t", "disk_total_bytes": None,
                    "disk_used_bytes": None, "disk_free_bytes": None, "data_dir_bytes": 2048,
                    "data_dir_files": 2, "data_dir_truncated": True, "largest_files": []},
        "workload": {"status": "ok", "source": "s", "measured_at": "t", "items": [
            {"name": "autonomous_life", "status": "unavailable", "reason": "status could not be read"}]},
    }
    cards = run_ui(tmp_path, {"c": {"ok": True, "data": data}})["c"]
    inf, spend, res, sto, work = (c["pairs"] for c in cards)
    assert inf["Tokens in / out"] == "Unknown / Unknown" and inf["Recorded cost"] == "Unknown"
    assert inf["Total latency"] == "Not measured" and inf["First-token latency"] == "Not measured"
    assert "Unknown" in inf["p / m"] and "0 ms" not in inf["p / m"]
    assert spend["Spent today (UTC)"] == "Unknown" and spend["Spent this month (UTC)"] == "Unknown"
    assert spend["Per-request limit"] == "Not configured" and spend["Daily limit"] == "Invalid configuration"
    assert spend["Monthly limit"] == "$5.0000"
    assert res["CPU (host)"] == "Unknown" and res["Mia memory (RSS)"] == "Unknown"
    assert res["Memory available / total"] == "Unknown / Unknown"
    assert sto["Disk free / total"] == "Unknown / Unknown"
    assert sto["Mia data folder"].startswith("at least 2 KB")
    assert work["Autonomous life"] == "status could not be read"
    assert "unknown, not zero" in cards[0]["notes"][0]
    assert any("lower bounds" in n for n in cards[3]["notes"])


def test_ui_shows_unavailable_sections_with_reason_and_endpoint_failure(tmp_path):
    bad = {"status": "unavailable", "source": "psutil", "measured_at": "t", "reason": "not installed"}
    cases = {
        "partial": {"ok": True, "data": {"usage": bad, "resources": bad, "storage": bad, "workload": bad}},
        "missing": {"ok": True, "data": {}},
        "failed": {"ok": False, "message": "Usage dashboard status failed: HTTP 500"},
    }
    out = run_ui(tmp_path, cases)
    assert all(c["state"] == "Unavailable" for c in out["partial"]) and len(out["partial"]) == 5
    assert out["partial"][2]["notes"][0] == "not installed"
    assert all(c["state"] == "Unavailable" for c in out["missing"])
    assert out["failed"][0]["notes"] == ["Usage dashboard status failed: HTTP 500"]


def test_ui_hostile_strings_are_inert_text(tmp_path):
    evil = "<img src=x onerror=alert(1)>"
    data = {"usage": {"status": "ok", "source": evil, "measured_at": evil, "window_days": 30,
                      "requests": 1, "paid_requests": 0, "non_local_requests": 0, "input_tokens": 1,
                      "output_tokens": 1, "cost_usd": 0, "spend": {}, "latency_ms": {}, "notes": [evil],
                      "by_model": [{"provider_id": evil, "model_id": evil, "requests": 1,
                                    "cost_usd": 0, "avg_total_ms": 1, "data_local": True}]},
            "resources": None, "storage": None, "workload": None}
    cards = run_ui(tmp_path, {"c": {"ok": True, "data": data}})["c"]
    assert evil in json.dumps(cards[0])  # present only as literal text content
    assert cards[2]["state"] == "Unavailable"
