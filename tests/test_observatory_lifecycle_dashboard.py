"""Real-Chromium tests of the experiment lifecycle controls on the observatory dashboard (MIA-OBS-08).

Real uvicorn server and real page against A2's backend. The owner's real experiment is never
touched: every test uses its own temporary database. Proves wiring, confirmation, acknowledgement and
failure display; says nothing about how the page looks to a person.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest
import uvicorn

from mia.observatory.api import create_app
from mia.observatory.experiment_export import verify_export
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

    def start(*, status=None):
        store = ObservatoryStore(tmp_path / "o.sqlite3")
        eid = None
        if status:
            eid = store.prepare_experiment(name="Lifecycle run", baseline_manifest=passing_manifest(),
                                           planned_days=5)["experiment_id"]
            if status == "running":
                store.start_experiment(eid)
                store.record_daily_snapshot(
                    experiment_id=eid, day_index=1, source_commit=BASE, snapshot={
                        "day_index": 1, "source_commit": BASE, "missing_day_indices": [],
                        "baseline_sha256": store.get_baseline(eid)["baseline_sha256"],
                        "activity": {"capabilities": {"available": True, "transitions": {},
                                                       "owner_approved_actions": 0}}})
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
        return store, eid, f"http://127.0.0.1:{port}"

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


def _open(browser, url, expect_state):
    page = browser.new_page()
    page.errors = []
    page.on("pageerror", lambda exc: page.errors.append(str(exc)))
    page.goto(url + "/")
    page.wait_for_function(
        "s => document.getElementById('experimentControlState').textContent === s", arg=expect_state)
    return page


def test_without_an_experiment_every_lifecycle_control_is_off_and_nothing_is_requested(browser, serve):
    _, _, url = serve()
    page = browser.new_page()
    requested = []
    page.on("request", lambda r: requested.append(r.url))
    page.goto(url + "/")
    page.wait_for_function("document.getElementById('experimentControlState').textContent !== ''")
    page.wait_for_timeout(500)
    for control in ("stopExperiment", "exportExperiment", "pauseAutoCapture", "resumeAutoCapture"):
        assert page.locator(f"#{control}").is_disabled(), control
    assert page.locator("#ackNoticeRow").is_hidden()
    assert not any("/export" in u or "/stop" in u for u in requested)
    page.close()


def test_start_needs_the_notice_acknowledged_and_sends_the_version(browser, serve):
    store, eid, url = serve(status="prepared")
    page = _open(browser, url, "Baseline frozen")
    assert page.locator("#startExperiment").is_disabled()          # not acknowledged yet
    page.click("#showPrivacyNotice")
    page.wait_for_selector("#privacyNoticeBox strong")
    notice = page.inner_text("#privacyNoticeBox")
    assert "Privacy notice · version 1" in notice and "Never collected" in notice
    assert "Resource caps" in notice
    assert page.locator("#ackNoticeRow").is_visible() and page.locator("#startExperiment").is_disabled()
    page.check("#ackNotice")
    page.wait_for_function("!document.getElementById('startExperiment').disabled")
    with page.expect_request(lambda r: r.url.endswith(f"/{eid}/start")) as sent:
        page.click("#startExperiment")
    assert json.loads(sent.value.post_data) == {"privacy_notice_version": 1}
    page.wait_for_function("document.getElementById('experimentControlState').textContent === 'Running'")
    started = [e for e in store.list_events(limit=50) if e["event_type"] == "experiment_started"]
    assert json.loads(started[0]["payload_json"])["privacy_notice_version_acknowledged"] == 1
    assert page.errors == []
    page.close()


def test_stop_needs_a_second_confirmation_and_keeps_the_evidence(browser, serve):
    store, eid, url = serve(status="running")
    page = _open(browser, url, "Running")
    assert page.locator("#stopExperiment").is_enabled() and page.locator("#stopConfirm").is_hidden()
    page.click("#stopExperiment")
    assert page.locator("#stopConfirm").is_visible()
    assert store.list_experiments()[0]["status"] == "running"      # asking changed nothing
    page.click("#cancelStop")
    assert page.locator("#stopConfirm").is_hidden() and store.list_experiments()[0]["status"] == "running"
    page.click("#stopExperiment")
    page.click("#confirmStop")
    page.wait_for_function("document.getElementById('lifecycleMessage').textContent.includes('stopped')")
    assert store.list_experiments()[0]["status"] == "stopped"
    assert "not marked completed" in page.inner_text("#lifecycleMessage")
    assert [e["event_type"] for e in store.list_events(limit=50)].count("experiment_stopped") == 1
    assert len(store.list_snapshots(eid)) >= 1                      # evidence kept
    assert page.locator("#stopExperiment").is_disabled()
    page.close()


def test_export_downloads_a_bundle_that_passes_the_offline_verifier(browser, serve):
    _, eid, url = serve(status="running")
    page = _open(browser, url, "Running")
    with page.expect_download() as download:
        page.click("#exportExperiment")
    suggested = download.value.suggested_filename
    assert suggested.startswith("mia-experiment-") and suggested.endswith(".json")
    bundle = json.loads(open(download.value.path(), encoding="utf-8").read())
    assert verify_export(bundle) == []
    page.wait_for_function("document.getElementById('lifecycleMessage').textContent.includes('Verify it offline')")
    assert "experiment_export verify" in page.inner_text("#lifecycleMessage")
    page.close()


def test_a_refused_export_shows_the_reason_and_downloads_nothing(browser, serve):
    _, _, url = serve(status="running")
    page = _open(browser, url, "Running")
    page.route("**/export", lambda route: route.fulfill(
        status=409, content_type="application/json",
        body=json.dumps({"detail": "Not exported: stored evidence failed verification (x)"})))
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    page.click("#exportExperiment")
    page.wait_for_function("document.getElementById('lifecycleMessage').textContent.includes('Not exported')")
    page.wait_for_timeout(300)
    assert downloads == []
    page.close()


@pytest.mark.parametrize("scheduler, pause_on, resume_on", [
    ({"enabled": True, "paused": False}, True, False),
    ({"enabled": True, "paused": True}, False, True),
    ({"enabled": False, "paused": False}, False, False),
])
def test_pause_and_resume_follow_the_scheduler_state(browser, serve, scheduler, pause_on, resume_on):
    _, _, url = serve(status="running")
    page = browser.new_page()
    page.route("**/api/experiments/scheduler/status", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(scheduler)))
    page.goto(url + "/")
    page.wait_for_function("document.getElementById('experimentSchedulerState').textContent !== ''")
    assert page.locator("#pauseAutoCapture").is_enabled() is pause_on
    assert page.locator("#resumeAutoCapture").is_enabled() is resume_on
    page.close()


def test_pause_calls_the_backend_and_reports_the_result(browser, serve):
    _, _, url = serve(status="running")
    page = browser.new_page()
    state = {"enabled": True, "paused": False}
    page.route("**/api/experiments/scheduler/status", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(state)))
    page.route("**/api/experiments/scheduler/pause", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(
            {"changed": True, "enabled": True, "paused": True})))
    page.goto(url + "/")
    page.wait_for_function("!document.getElementById('pauseAutoCapture').disabled")
    state["paused"] = True
    page.click("#pauseAutoCapture")
    page.wait_for_function("document.getElementById('lifecycleMessage').textContent.includes('paused')")
    page.wait_for_function("!document.getElementById('resumeAutoCapture').disabled")
    assert page.locator("#pauseAutoCapture").is_disabled()
    page.close()


def test_notice_text_from_the_server_is_never_interpreted_as_html(browser, serve):
    _, _, url = serve(status="prepared")
    page = browser.new_page()
    hostile = "<img src=x onerror=\"window.__pwned=1\">"
    real = {"version": 1, "summary": hostile, "stored": hostile, "collected": [
        {"group": hostile, "fields": [{"field": hostile, "what": hostile}]}],
        "never_collected": [hostile], "controls": {"stop": hostile}, "resource_caps": {}}
    page.route("**/api/experiments/privacy-notice", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(real)))
    page.goto(url + "/")
    page.wait_for_function("document.getElementById('experimentControlState').textContent === 'Baseline frozen'")
    page.click("#showPrivacyNotice")
    page.wait_for_selector("#privacyNoticeBox strong")
    assert hostile in page.inner_text("#privacyNoticeBox")
    assert page.locator("#privacyNoticeBox img").count() == 0 and page.evaluate("window.__pwned") is None
    page.close()
