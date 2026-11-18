import sqlite3

import pytest

from mia.observatory.store import ObservatoryStore


def baseline_manifest():
    return {
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "configuration_hash": "sha256:" + "a" * 64,
        "benchmark_suite_id": "mia-foundation-v2",
        "environment": {
            "os": "Windows",
            "python": "3.13",
            "platform": "Windows-test-platform",
            "machine": "AMD64",
        },
        "fixed_benchmark_results": {
            "tests": 6,
            "passed": 6,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": 1.25,
            "pytest_exit_code": 0,
            "suite_id": "mia-foundation-v2",
        },
        "baseline_results": {
            "tests": 6,
            "passed": 6,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "ruff_passed": True,
            "ruff_exit_code": 0,
            "pytest_exit_code": 0,
            "capture_method": "local-host-fixed-benchmark-and-regression-v3",
            "capture_host": "test-machine",
        },
        "verification_passed": True,
    }


def test_experiment_requires_verified_baseline(tmp_path):
    store = ObservatoryStore(tmp_path / "experiment.sqlite3")
    invalid = baseline_manifest()
    invalid["verification_passed"] = False

    with pytest.raises(ValueError, match="must pass"):
        store.prepare_experiment(name="Mia 30-day test", baseline_manifest=invalid)


def test_baseline_and_daily_snapshots_are_immutable(tmp_path):
    database = tmp_path / "experiment.sqlite3"
    store = ObservatoryStore(database)
    prepared = store.prepare_experiment(
        name="Mia 30-day test",
        baseline_manifest=baseline_manifest(),
        planned_days=30,
    )
    experiment_id = prepared["experiment_id"]
    assert len(prepared["baseline_sha256"]) == 64
    assert store.list_snapshots(experiment_id)[0]["day_index"] == 0

    store.start_experiment(experiment_id)
    snapshot_id = store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=1,
        source_commit="1123456789abcdef0123456789abcdef01234567",
        snapshot={"validated_capabilities": 1, "regressions": 0},
    )
    assert len(snapshot_id) == 32

    with pytest.raises(ValueError, match="already exists"):
        store.record_daily_snapshot(
            experiment_id=experiment_id,
            day_index=1,
            source_commit="2123456789abcdef0123456789abcdef01234567",
            snapshot={"attempted_overwrite": True},
        )

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE daily_snapshots SET snapshot_json = '{}' WHERE snapshot_id = ?",
                (snapshot_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "DELETE FROM experiment_baselines WHERE experiment_id = ?",
                (experiment_id,),
            )


def test_only_one_experiment_can_be_prepared_or_running(tmp_path):
    store = ObservatoryStore(tmp_path / "experiment.sqlite3")
    store.prepare_experiment(
        name="First",
        baseline_manifest=baseline_manifest(),
    )
    with pytest.raises(ValueError, match="already prepared or running"):
        store.prepare_experiment(
            name="Second",
            baseline_manifest=baseline_manifest(),
        )


def test_experiment_rejects_client_claim_without_local_benchmark_evidence(tmp_path):
    store = ObservatoryStore(tmp_path / "experiment.sqlite3")
    invalid = baseline_manifest()
    invalid["baseline_results"] = {
        "checks": 12,
        "passed": 12,
        "failed": 0,
    }
    with pytest.raises(ValueError, match="local pytest evidence"):
        store.prepare_experiment(name="No fake baseline", baseline_manifest=invalid)


def test_experiment_rejects_skipped_local_baseline_checks(tmp_path):
    store = ObservatoryStore(tmp_path / "experiment.sqlite3")
    invalid = baseline_manifest()
    invalid["baseline_results"]["skipped"] = 1
    invalid["baseline_results"]["passed"] = 5
    invalid["baseline_results"]["tests"] = 6
    with pytest.raises(ValueError, match="fully passing"):
        store.prepare_experiment(name="Skipped baseline", baseline_manifest=invalid)


def test_experiment_baseline_can_be_reloaded_and_hash_verified(tmp_path):
    store = ObservatoryStore(tmp_path / "baseline.sqlite3")
    prepared = store.prepare_experiment(
        name="Day 0 reload",
        baseline_manifest=baseline_manifest(),
    )
    baseline = store.get_baseline(prepared["experiment_id"])
    assert baseline is not None
    assert baseline["baseline_sha256"] == prepared["baseline_sha256"]
    assert baseline["manifest"]["benchmark_suite_id"] == "mia-foundation-v2"


def test_snapshot_due_day_uses_actual_elapsed_utc_time_and_marks_missed_days(tmp_path):
    from datetime import UTC, datetime, timedelta

    store = ObservatoryStore(tmp_path / "snapshot-due.sqlite3")
    prepared = store.prepare_experiment(
        name="Snapshot schedule",
        baseline_manifest=baseline_manifest(),
        planned_days=5,
    )
    experiment_id = prepared["experiment_id"]
    before_start = store.get_snapshot_status(
        experiment_id,
        now=datetime.now(UTC) + timedelta(days=5),
    )
    assert before_start["due"] is False
    store.start_experiment(experiment_id)
    with sqlite3.connect(store.database_path) as connection:
        started = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started, experiment_id),
        )
        connection.commit()

    day_one = store.get_snapshot_status(experiment_id)
    assert day_one["due"] is True
    assert day_one["day_index"] == 1
    assert day_one["days_missed"] == []

    now = datetime.fromisoformat(started) + timedelta(days=3, hours=1)
    day_three = store.get_snapshot_status(experiment_id, now=now)
    assert day_three["due"] is True
    assert day_three["day_index"] == 3
    assert day_three["days_missed"] == [1, 2]

    store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=3,
        source_commit="3123456789abcdef0123456789abcdef01234567",
        snapshot={"verification_passed": True},
    )
    already_captured = store.get_snapshot_status(experiment_id, now=now)
    assert already_captured["due"] is False
    assert already_captured["latest_day_index"] == 3


def test_final_daily_snapshot_completes_the_experiment(tmp_path):
    store = ObservatoryStore(tmp_path / "final-day.sqlite3")
    prepared = store.prepare_experiment(
        name="One-day experiment",
        baseline_manifest=baseline_manifest(),
        planned_days=1,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)
    store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=1,
        source_commit="4123456789abcdef0123456789abcdef01234567",
        snapshot={"verification_passed": True},
    )
    experiment = store.list_experiments()[0]
    assert experiment["status"] == "completed"


def test_experiment_requires_fixed_benchmark_evidence(tmp_path):
    store = ObservatoryStore(tmp_path / "experiment.sqlite3")
    invalid = baseline_manifest()
    invalid.pop("fixed_benchmark_results")

    with pytest.raises(ValueError, match="fixed_benchmark_results"):
        store.prepare_experiment(name="No fixed benchmark", baseline_manifest=invalid)


def test_report_request_closes_expired_run_as_incomplete_without_status_poll(tmp_path):
    from datetime import UTC, datetime, timedelta

    database = tmp_path / "expired-report.sqlite3"
    store = ObservatoryStore(database)
    prepared = store.prepare_experiment(
        name="Expired report",
        baseline_manifest=baseline_manifest(),
        planned_days=2,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)

    started_at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        connection.commit()

    report = store.build_experiment_report(experiment_id)

    assert report is not None
    assert report["experiment"]["status"] == "incomplete"
    assert report["experiment"]["conclusion"] == "incomplete"
    assert report["experiment"]["completed_at"]
    assert report["report_ready"] is True
    assert report["days"]["recorded"] == [0]
    assert report["days"]["missing"] == [1, 2]
    assert report["comparison"]["comparison_is_final"] is False
    assert report["comparison"]["compared_day"] is None
    assert len([
        event for event in report["events"]
        if event["event_type"] == "experiment_incomplete"
    ]) == 1


def test_report_uses_snapshot_embedded_offline_days_if_event_write_was_interrupted(tmp_path):
    from datetime import UTC, datetime, timedelta

    database = tmp_path / "snapshot-unavailable-days.sqlite3"
    store = ObservatoryStore(database)
    prepared = store.prepare_experiment(
        name="Snapshot embedded offline days",
        baseline_manifest=baseline_manifest(),
        planned_days=4,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)
    started_at = (datetime.now(UTC) - timedelta(days=3, hours=1)).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        connection.commit()

    # Model a host crash after snapshot commit but before the unavailable-day
    # event is written. The immutable snapshot still carries the evidence.
    store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=3,
        source_commit="5123456789abcdef0123456789abcdef01234567",
        snapshot={
            "day_index": 3,
            "source_commit": "5123456789abcdef0123456789abcdef01234567",
            "missing_day_indices": [1, 2],
        },
    )

    report = store.build_experiment_report(experiment_id)

    assert report is not None
    assert report["days"]["recorded"] == [0, 3]
    assert report["days"]["missing"] == [1, 2, 4]
    assert report["days"]["unavailable"] == [1, 2]
    assert report["days"]["unaccounted"] == [4]
