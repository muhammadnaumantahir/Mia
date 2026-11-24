"""MIA-OBS-03: honest, pausable, idempotent daily snapshot scheduling.

Every day the scheduler does not capture must leave an explicit durable record (paused, resource
pressure, missing telemetry, failed, nothing-to-do), pause state must survive restarts, and a
capture must be idempotent. Fake resource monitor and fake baseline only; no real benchmark runs.
"""

import asyncio
import json
import socket
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.snapshot_capture import ExperimentSnapshotCapturer, SnapshotCaptureError
from mia.observatory.snapshot_scheduler import (
    NOOP_DAY_EVENT,
    PAUSED_EVENT,
    RESUMED_EVENT,
    SKIPPED_PAUSED_EVENT,
    TELEMETRY_DEFERRED_EVENT,
    ExperimentSnapshotScheduler,
    SchedulerNotEnabledError,
)
from mia.observatory.store import ObservatoryStore

from .test_experiment_scheduler import (
    FakeResources,
    create_due_experiment,
    passing_manifest,
)


def build(tmp_path, *, resources=None, enabled=True, store=None, experiment=True, **kwargs):
    if store is None:
        store, experiment_id = create_due_experiment(tmp_path) if experiment else (
            ObservatoryStore(tmp_path / "empty.sqlite3"), None)
    else:
        experiment_id = None
    resources = resources or FakeResources()
    capturer = ExperimentSnapshotCapturer(
        observatory=store, baseline_capture=lambda root: passing_manifest(),
        resource_monitor=resources, repo_root=tmp_path)
    scheduler = ExperimentSnapshotScheduler(store, capturer, resources, enabled=enabled,
                                            interval_seconds=15, **kwargs)
    return store, experiment_id, scheduler, resources


def events(store, *types):
    return [e for e in store.list_events(500) if not types or e["event_type"] in types]


def snapshot_days(store, experiment_id):
    return [s["day_index"] for s in store.list_snapshots(experiment_id)]


# ---- pause / resume -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paused_scheduler_records_the_day_and_captures_nothing(tmp_path):
    store, eid, scheduler, resources = build(tmp_path)
    assert scheduler.pause() is True
    status = scheduler.status()
    assert status["paused"] is True and status["paused_at"] and status["enabled"] is True
    first = await scheduler.run_once()
    again = await scheduler.run_once()
    assert [o["status"] for o in first + again] == ["paused", "paused"]
    assert snapshot_days(store, eid) == [0] and resources.calls == 0  # no telemetry or capture work
    skipped = events(store, SKIPPED_PAUSED_EVENT)
    assert len(skipped) == 1 and skipped[0]["day_index"] == 1 and skipped[0]["outcome"] == "skipped"
    assert skipped[0]["experiment_id"] == eid


@pytest.mark.asyncio
async def test_resume_captures_the_still_due_day(tmp_path):
    store, eid, scheduler, _ = build(tmp_path)
    scheduler.pause()
    await scheduler.run_once()
    assert scheduler.resume() is True
    result = await scheduler.run_once()
    assert result[0]["status"] == "recorded" and snapshot_days(store, eid) == [0, 1]
    assert scheduler.status()["paused"] is False and scheduler.status()["paused_at"] is None
    assert [e["event_type"] for e in reversed(events(store, PAUSED_EVENT, RESUMED_EVENT))] == [
        PAUSED_EVENT, RESUMED_EVENT]


def test_pause_state_survives_a_restart(tmp_path):
    store, _, scheduler, _ = build(tmp_path)
    scheduler.pause()
    _, _, restarted, _ = build(tmp_path, store=store)
    assert restarted.paused is True and restarted.status()["paused"] is True
    restarted.resume()
    _, _, again, _ = build(tmp_path, store=store)
    assert again.paused is False


def test_pause_and_resume_are_idempotent_and_do_not_spam_the_audit_trail(tmp_path):
    store, _, scheduler, _ = build(tmp_path)
    assert scheduler.resume() is False  # not paused: nothing to do, nothing recorded
    assert scheduler.pause() is True and scheduler.pause() is False
    assert scheduler.resume() is True and scheduler.resume() is False
    assert len(events(store, PAUSED_EVENT)) == 1 and len(events(store, RESUMED_EVENT)) == 1


def test_a_scheduler_the_owner_did_not_opt_in_to_cannot_be_paused(tmp_path):
    store, _, scheduler, _ = build(tmp_path, enabled=False)
    for action in (scheduler.pause, scheduler.resume):
        with pytest.raises(SchedulerNotEnabledError, match="MIA_EXPERIMENT_AUTO_CAPTURE"):
            action()
    assert events(store, PAUSED_EVENT, RESUMED_EVENT) == []
    assert scheduler.status()["paused"] is False


# ---- every non-capture day leaves an explicit record --------------------------------------

class BrokenTelemetry:
    def snapshot(self):
        raise RuntimeError("psutil exploded at C:\\secret\\path with token=abc")


class FailingCapturer:
    def capture(self, experiment_id):
        raise SnapshotCaptureError(409, "baseline verification failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected_event,expected_outcome", [
    ("resource_pressure", "daily_snapshot_deferred_resource_pressure", "deferred"),
    ("telemetry_unavailable", TELEMETRY_DEFERRED_EVENT, "deferred"),
    ("paused", SKIPPED_PAUSED_EVENT, "skipped"),
    ("capture_failed", "daily_snapshot_auto_capture_failed", "failed"),
])
async def test_each_non_capture_day_leaves_one_explicit_event_and_no_snapshot(
        tmp_path, case, expected_event, expected_outcome):
    resources = {"resource_pressure": FakeResources(cpu=99.0),
                 "telemetry_unavailable": BrokenTelemetry()}.get(case, FakeResources())
    store, eid, scheduler, _ = build(tmp_path, resources=resources)
    if case == "paused":
        scheduler.pause()
    if case == "capture_failed":
        scheduler.capturer = FailingCapturer()
    outcome = await scheduler.run_once()
    assert len(outcome) == 1 and outcome[0]["status"] != "recorded"
    matching = events(store, expected_event)
    assert len(matching) == 1 and matching[0]["outcome"] == expected_outcome
    assert matching[0]["day_index"] == 1 and matching[0]["experiment_id"] == eid
    assert snapshot_days(store, eid) == [0]  # nothing was invented


@pytest.mark.asyncio
async def test_telemetry_event_names_the_error_type_but_never_its_text(tmp_path):
    store, _, scheduler, _ = build(tmp_path, resources=BrokenTelemetry())
    await scheduler.run_once()
    event = events(store, TELEMETRY_DEFERRED_EVENT)[0]
    dumped = json.dumps(event)
    assert json.loads(event["payload_json"]) == {"error_type": "RuntimeError"}
    assert "secret" not in dumped and "token=abc" not in dumped


@pytest.mark.asyncio
async def test_a_deferred_day_is_captured_once_telemetry_recovers(tmp_path):
    resources = FakeResources(cpu=99.0)
    store, eid, scheduler, _ = build(tmp_path, resources=resources)
    await scheduler.run_once()
    resources.cpu = 10.0
    scheduler._last_attempt.clear()  # the retry spacing elapsed
    assert (await scheduler.run_once())[0]["status"] == "recorded"
    assert snapshot_days(store, eid) == [0, 1]


# ---- no-op days ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_day_with_nothing_running_gets_exactly_one_noop_record(tmp_path):
    store, _, scheduler, resources = build(tmp_path, experiment=False)
    assert await scheduler.run_once() == [] and await scheduler.run_once() == []
    noops = events(store, NOOP_DAY_EVENT)
    assert len(noops) == 1 and noops[0]["outcome"] == "no_op"
    assert json.loads(noops[0]["payload_json"])["date_utc"] == datetime.now(UTC).date().isoformat()
    assert resources.calls == 0
    _, _, restarted, _ = build(tmp_path, store=store)  # restart on the same day: still one
    await restarted.run_once()
    assert len(events(store, NOOP_DAY_EVENT)) == 1


@pytest.mark.asyncio
async def test_noop_day_is_recorded_again_on_a_new_utc_day(tmp_path, monkeypatch):
    store, _, scheduler, _ = build(tmp_path, experiment=False)
    await scheduler.run_once()
    scheduler._noop_day = "2000-01-01"  # as if the loop last noted a day long ago
    with store._connection() as db:  # and yesterday's record is the only one in the store
        db.execute("UPDATE events SET occurred_at = '2000-01-01T10:00:00+00:00' WHERE event_type = ?",
                   (NOOP_DAY_EVENT,))
    await scheduler.run_once()
    assert len(events(store, NOOP_DAY_EVENT)) == 2


@pytest.mark.asyncio
async def test_no_noop_record_when_paused_or_when_an_experiment_is_running(tmp_path):
    store, _, paused_scheduler, _ = build(tmp_path, experiment=False)
    paused_scheduler.pause()
    await paused_scheduler.run_once()
    assert events(store, NOOP_DAY_EVENT) == []
    other = tmp_path / "busy"
    other.mkdir()
    store2, _, busy, _ = build(other)  # a running, due experiment
    await busy.run_once()
    assert events(store2, NOOP_DAY_EVENT) == []


# ---- idempotency and bounds ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_repeated_and_concurrent_runs_capture_each_day_exactly_once(tmp_path):
    store, eid, scheduler, _ = build(tmp_path)
    results = await asyncio.gather(scheduler.run_once(), scheduler.run_once(), scheduler.run_once())
    assert sum(1 for r in results for o in r if o["status"] == "recorded") == 1
    assert await scheduler.run_once() == []  # nothing is due any more
    assert snapshot_days(store, eid) == [0, 1]
    assert len(events(store, "daily_snapshot_captured")) == 1
    assert events(store, "daily_snapshot_auto_capture_failed") == []  # no false failure from the race


@pytest.mark.asyncio
async def test_capture_path_performs_no_network_activity(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the snapshot capture path attempted network access")

    for name in ("connect", "connect_ex", "sendto", "bind", "listen"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    store, eid, scheduler, _ = build(tmp_path)
    assert (await scheduler.run_once())[0]["status"] == "recorded"
    assert snapshot_days(store, eid) == [0, 1]


def test_scheduler_is_opt_in_and_resource_bounded_by_construction(tmp_path):
    store, _, scheduler, _ = build(tmp_path, enabled=False)
    assert scheduler.status()["enabled"] is False and scheduler.start() is None
    assert asyncio.run(scheduler.run_once()) == []
    with pytest.raises(ValueError):
        build(tmp_path, max_cpu_percent=0)


# ---- store query ---------------------------------------------------------------------------

def test_find_events_filters_orders_and_clamps(tmp_path):
    store = ObservatoryStore(tmp_path / "e.sqlite3")
    ids = {}
    for index, (etype, exp, day) in enumerate([("a", "x", 1), ("b", "x", 2), ("a", "y", 1), ("a", "x", 2)]):
        ids[index] = store.record_event(category="experiment", event_type=etype, summary=str(index),
                                        experiment_id=exp, day_index=day)
    found = store.find_events(("a",))
    assert [e["summary"] for e in found] == ["3", "2", "0"]  # newest first, same-instant ties by insertion
    assert [e["summary"] for e in store.find_events(("a", "b"), experiment_id="x", day_index=2)] == ["3", "1"]
    assert store.find_events(("a",), since="2999-01-01") == []
    assert len(store.find_events(("a",), limit=1)) == 1 and store.find_events(()) == []
    assert store.find_events(("a'; DROP TABLE events; --",)) == []
    assert store.list_events(10)  # table intact


# ---- API ----------------------------------------------------------------------------------

def client(tmp_path, monkeypatch, enabled):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_EXPERIMENT_AUTO_CAPTURE", "true" if enabled else "false")
    return TestClient(create_app(store=ObservatoryStore(tmp_path / "obs.sqlite3")))


def test_api_pause_resume_and_status(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch, True)
    assert c.get("/api/experiments/scheduler/status").json()["paused"] is False
    paused = c.post("/api/experiments/scheduler/pause")
    assert paused.status_code == 200 and paused.json()["changed"] is True and paused.json()["paused"] is True
    assert c.post("/api/experiments/scheduler/pause").json()["changed"] is False
    assert c.get("/api/experiments/scheduler/status").json()["paused"] is True
    resumed = c.post("/api/experiments/scheduler/resume").json()
    assert resumed["changed"] is True and resumed["paused"] is False
    recorded = c.get("/api/observatory/events?limit=50")
    assert recorded.status_code == 200
    kinds = [e["event_type"] for e in recorded.json()["items"]]
    assert kinds.count(PAUSED_EVENT) == 1 and kinds.count(RESUMED_EVENT) == 1  # the repeat pause logged nothing


def test_api_refuses_pause_when_not_opted_in_and_is_post_only(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch, False)
    for path in ("pause", "resume"):
        response = c.post(f"/api/experiments/scheduler/{path}")
        assert response.status_code == 409 and "MIA_EXPERIMENT_AUTO_CAPTURE" in response.json()["detail"]
        assert c.get(f"/api/experiments/scheduler/{path}").status_code in (404, 405)
    assert c.get("/api/experiments/scheduler/status").json()["enabled"] is False


# ---- the final report accounts for paused days explicitly ---------------------------------

@pytest.mark.asyncio
async def test_report_lists_a_paused_day_as_unavailable_not_unaccounted(tmp_path):
    store, eid, scheduler, _ = build(tmp_path)
    scheduler.pause()
    await scheduler.run_once()  # Day 1 is due but paused
    days = store.build_experiment_report(eid)["days"]
    assert days["unavailable"] == [1] and days["unaccounted"] == [2, 3] and days["recorded"] == [0]


@pytest.mark.asyncio
async def test_a_paused_day_that_is_captured_after_resume_is_no_longer_missing(tmp_path):
    store, eid, scheduler, _ = build(tmp_path)
    scheduler.pause()
    await scheduler.run_once()
    scheduler.resume()
    await scheduler.run_once()
    days = store.build_experiment_report(eid)["days"]
    assert days["recorded"] == [0, 1] and days["unavailable"] == [] and 1 not in days["missing"]


def test_report_ignores_malformed_paused_day_indices(tmp_path):
    store, eid, _, _ = build(tmp_path)
    for day in (0, 99):  # outside 1..planned_days
        store.record_event(category="experiment", event_type=SKIPPED_PAUSED_EVENT, summary="x",
                           experiment_id=eid, day_index=day)
    store.record_event(category="experiment", event_type=SKIPPED_PAUSED_EVENT, summary="x", experiment_id=eid)
    days = store.build_experiment_report(eid)["days"]
    assert days["unavailable"] == [] and days["unaccounted"] == [1, 2, 3]
