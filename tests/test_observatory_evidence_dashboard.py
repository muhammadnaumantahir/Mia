"""Real Chromium test of the dashboard Evidence panel (MIA-OBS-06): real uvicorn server, real page.

Proves rendering, drill-down to raw records, honest empty/gap states and that stored text is never
interpreted as HTML. Says nothing about how the charts look to a person.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from mia.observatory.api import create_app
from mia.observatory.benchmarks import record_run
from mia.observatory.store import ObservatoryStore
from tests.test_experiment_scheduler import passing_manifest

playwright_sync = pytest.importorskip("playwright.sync_api")
BASE = passing_manifest()["source_commit"]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def serve(tmp_path):
    servers = []

    def start(*, name=None, days=(1, 2), planned=5):
        store = ObservatoryStore(tmp_path / "o.sqlite3")
        if name:
            eid = store.prepare_experiment(name=name, baseline_manifest=passing_manifest(),
                                           planned_days=planned)["experiment_id"]
            store.start_experiment(eid)
            for day in days:
                commit = BASE if day == days[0] else "newcommit"
                store.record_daily_snapshot(
                    experiment_id=eid, day_index=day, source_commit=commit, snapshot={
                        "day_index": day, "source_commit": commit, "missing_day_indices": [],
                        "activity": {"capabilities": {"available": True, "transitions": {},
                                                       "owner_approved_actions": 0}}})
                store.record_metric(day_index=day, metric_name="cpu_percent",
                                    value=10.0 * day, unit="percent", experiment_id=eid)
            record_run(store, {"suite_id": "mia-scripted-memory", "suite_version": "sha256:v1",
                               "evidence": "deterministic_scripted_provider",
                               "conditions": {"models": [], "commit": BASE}, "passed": 4,
                               "total": 4, "items": {f"s{i}": True for i in range(4)}},
                       day_index=days[0], experiment_id=eid)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(store), host="127.0.0.1", port=port,
                                               log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        servers.append(server)
        return f"http://127.0.0.1:{port}"

    yield start
    for server in servers:
        server.should_exit = True


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


def _open(browser, url):
    page = browser.new_page()
    page.errors = []
    page.on("pageerror", lambda exc: page.errors.append(str(exc)))
    page.goto(url + "/")
    page.wait_for_function("document.getElementById('experimentControlState').textContent !== ''")
    return page


def test_without_an_experiment_the_evidence_button_is_disabled_and_nothing_is_drawn(browser, serve):
    page = _open(browser, serve())
    assert page.locator("#buildEvidenceReport").is_disabled()
    assert page.locator("#evidenceReport").is_hidden()
    assert page.locator("#evidenceReport svg").count() == 0
    page.close()


def test_evidence_panel_shows_coverage_trends_candidates_and_versions(browser, serve):
    page = _open(browser, serve(name="Browser run"))
    page.wait_for_function("!document.getElementById('buildEvidenceReport').disabled")
    page.click("#buildEvidenceReport")
    page.wait_for_selector("[data-section='coverage']")
    assert "Captured 2 of 5 planned days" in page.inner_text("[data-section='coverage']")
    assert "missing: 3, 4, 5" in page.inner_text("[data-section='coverage']")
    metric = page.locator("[data-metric='cpu_percent']")
    text = metric.inner_text()
    assert "day 1 → 2: 10 → 20" in text and "candidates: commit" in text
    assert "causal claim: not allowed" in text
    assert metric.locator("svg line").count() == 1 and metric.locator("svg circle").count() == 2
    assert "mia-scripted-memory" in page.inner_text("[data-suite='mia-scripted-memory']")
    versions = page.inner_text("[data-section='versions']")
    assert "report hash " in versions and "baseline " in versions
    page.close()


def test_drill_down_shows_the_raw_stored_records(browser, serve):
    page = _open(browser, serve(name="Drill run"))
    page.wait_for_function("!document.getElementById('buildEvidenceReport').disabled")
    page.click("#buildEvidenceReport")
    page.wait_for_selector("[data-metric='cpu_percent'] button")
    page.click("[data-metric='cpu_percent'] button >> nth=1")
    page.wait_for_function("document.getElementById('evidenceRecord').textContent.includes('sample_id')")
    record = page.inner_text("#evidenceRecord")
    assert '"metric_name": "cpu_percent"' in record and '"value": 20,' in record and 'definition_version' in record
    page.click("[data-suite='mia-scripted-memory'] button")
    page.wait_for_function("document.getElementById('evidenceRecord').textContent.includes('benchmark_run')")
    assert "suite_version" in page.inner_text("#evidenceRecord")
    page.close()


def test_days_without_evidence_are_drawn_dashed(browser, serve):
    page = _open(browser, serve(name="Gap run", days=(1, 4)))
    page.wait_for_function("!document.getElementById('buildEvidenceReport').disabled")
    page.click("#buildEvidenceReport")
    page.wait_for_selector("[data-metric='cpu_percent'] svg")
    assert page.locator("[data-metric='cpu_percent'] svg line[stroke-dasharray]").count() == 1
    assert "no evidence on days 2, 3" in page.inner_text("[data-metric='cpu_percent']")
    page.close()


def test_stored_text_is_never_interpreted_as_html(browser, serve):
    hostile = "<img src=x onerror=\"window.__pwned=1\">"
    page = _open(browser, serve(name=hostile))
    page.wait_for_function("!document.getElementById('buildEvidenceReport').disabled")
    page.click("#buildEvidenceReport")
    page.wait_for_selector("[data-section='coverage']")
    assert hostile in page.inner_text("#evidenceReport")          # shown as text
    assert page.locator("#evidenceReport img").count() == 0
    assert page.evaluate("window.__pwned") is None
    page.close()


def test_the_page_script_runs_to_the_end_and_loads_the_experiment_on_its_own(browser, serve):
    """Regression: `$` was undefined, a top-level call threw and the startup calls never ran."""
    page = _open(browser, serve(name="Auto load"))
    page.wait_for_function("document.getElementById('experimentControlState').textContent === 'Running'")
    assert page.errors == []
    assert page.locator("#buildEvidenceReport").is_enabled()
    page.close()
