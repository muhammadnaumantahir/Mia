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
        "baseline_results": {
            "tests": 6,
            "passed": 6,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "ruff_passed": True,
            "ruff_exit_code": 0,
            "pytest_exit_code": 0,
            "capture_method": "local-host-ruff-pytest-v2",
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
