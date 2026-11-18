import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from mia.observatory.snapshot_capture import ExperimentSnapshotCapturer
from mia.observatory.snapshot_scheduler import ExperimentSnapshotScheduler
from mia.observatory.store import ObservatoryStore


def passing_manifest():
    return {
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "configuration_hash": "sha256:" + "a" * 64,
        "benchmark_suite_id": "mia-foundation-v2",
        "environment": {
            "os": "Windows",
            "python": "3.13",
            "platform": "Windows scheduler test host",
            "machine": "AMD64",
        },
        "baseline_results": {
            "tests": 10,
            "passed": 10,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "ruff_passed": True,
            "ruff_exit_code": 0,
            "pytest_exit_code": 0,
            "capture_method": "local-host-ruff-pytest-v2",
            "capture_host": "scheduler-test-host",
        },
        "verification_passed": True,
    }


class FakeResources:
    def __init__(self, cpu=10.0, memory=30.0):
        self.cpu = cpu
        self.memory = memory
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "cpu_percent": self.cpu,
            "memory_percent": self.memory,
            "memory_available_bytes": 1000,
            "memory_total_bytes": 2000,
        }


def create_due_experiment(tmp_path):
    store = ObservatoryStore(tmp_path / "scheduler.sqlite3")
    baseline = store.prepare_experiment(
        name="Scheduler test",
        baseline_manifest=passing_manifest(),
        planned_days=3,
    )
    experiment_id = baseline["experiment_id"]
    store.start_experiment(experiment_id)
    started_at = (datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        connection.commit()
    return store, experiment_id


@pytest.mark.asyncio
async def test_scheduler_captures_due_snapshot_when_resource_limits_allow(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    resources = FakeResources()
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=resources,
        repo_root=tmp_path,
    )
    scheduler = ExperimentSnapshotScheduler(
        store, capturer, resources, enabled=True, interval_seconds=15
    )

    result = await scheduler.run_once()

    assert result[0]["status"] == "recorded"
    assert result[0]["day_index"] == 1
    assert [item["day_index"] for item in store.list_snapshots(experiment_id)] == [0, 1]


@pytest.mark.asyncio
async def test_scheduler_defers_capture_under_resource_pressure(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    resources = FakeResources(cpu=92.0, memory=30.0)
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=resources,
        repo_root=tmp_path,
    )
    scheduler = ExperimentSnapshotScheduler(
        store, capturer, resources, enabled=True, interval_seconds=15
    )

    result = await scheduler.run_once()

    assert result[0]["status"] == "deferred"
    assert result[0]["reason"] == "resource_limit"
    assert [item["day_index"] for item in store.list_snapshots(experiment_id)] == [0]
    assert any(
        event["event_type"] == "daily_snapshot_deferred_resource_pressure"
        for event in store.list_events(limit=20)
    )


@pytest.mark.asyncio
async def test_scheduler_ignores_experiments_that_are_not_running(tmp_path):
    store = ObservatoryStore(tmp_path / "prepared.sqlite3")
    store.prepare_experiment(
        name="Prepared only",
        baseline_manifest=passing_manifest(),
        planned_days=3,
    )
    resources = FakeResources()
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=resources,
        repo_root=tmp_path,
    )
    scheduler = ExperimentSnapshotScheduler(
        store, capturer, resources, enabled=True, interval_seconds=15
    )

    result = await scheduler.run_once()

    assert result == []
    assert resources.calls == 0


@pytest.mark.asyncio
async def test_scheduler_is_disabled_by_default(tmp_path):
    store, _ = create_due_experiment(tmp_path)
    resources = FakeResources()
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=resources,
        repo_root=tmp_path,
    )
    scheduler = ExperimentSnapshotScheduler(store, capturer, resources)

    assert scheduler.status()["enabled"] is False
    assert await scheduler.run_once() == []
    assert resources.calls == 0


@pytest.mark.asyncio
async def test_scheduler_marks_run_incomplete_after_final_day_window_is_missed(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    now = datetime.now(UTC)
    started_at = (now - timedelta(days=4)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        connection.commit()

    status = store.get_snapshot_status(experiment_id, now=now)

    assert status is not None
    assert status["status"] == "incomplete"
    assert status["due"] is False
    assert "final daily snapshot was not captured" in status["reason"]
    assert store.list_experiments()[0]["status"] == "incomplete"
    incomplete_events = [
        event for event in store.list_events(limit=20)
        if event["event_type"] == "experiment_incomplete"
    ]
    assert len(incomplete_events) == 1
    assert json.loads(incomplete_events[0]["payload_json"]) == {
        "planned_days": 3,
        "latest_recorded_day": 0,
        "elapsed_day": 4,
        "final_day_snapshot_missing": True,
    }

    # Repeated polling must not append duplicate terminal events.
    store.get_snapshot_status(experiment_id, now=now)
    assert len([
        event for event in store.list_events(limit=20)
        if event["event_type"] == "experiment_incomplete"
    ]) == 1


def test_final_planned_day_remains_capturable_until_its_24_hour_window_ends(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    now = datetime.now(UTC)
    started_at = (now - timedelta(days=3, hours=1)).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        connection.commit()

    status = store.get_snapshot_status(experiment_id, now=now)

    assert status is not None
    assert status["status"] == "running"
    assert status["day_index"] == 3
    assert status["due"] is True
