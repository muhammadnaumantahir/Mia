import sqlite3

import pytest

from mia.observatory.store import ObservatoryStore


def baseline_manifest():
    return {
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "configuration_hash": "sha256:configuration-baseline",
        "benchmark_suite_id": "mia-foundation-v1",
        "environment": {"os": "Windows", "python": "3.13"},
        "baseline_results": {"checks": 12, "passed": 12, "failed": 0},
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
