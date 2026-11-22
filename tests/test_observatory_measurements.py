"""Measurement definitions and evidence integrity (MIA-OBS-01)."""

import json
import sqlite3

import pytest

from mia.observatory.measurements import (
    METRIC_DEFINITIONS,
    validate_sample,
)
from mia.observatory.snapshot_capture import ExperimentSnapshotCapturer
from mia.observatory.store import ObservatoryStore
from tests.test_experiment_scheduler import FakeResources, create_due_experiment, passing_manifest


def test_known_metric_must_use_its_defined_unit_and_range():
    for bad in (("test_pass_rate", 50, "seconds"), ("cpu_percent", 101, "percent"),
                ("test_pass_rate", -1, "percent"), ("fixed_benchmark_duration_seconds", 5, "percent")):
        with pytest.raises(ValueError):
            validate_sample(*bad)
    assert validate_sample("fixed_benchmark_duration_seconds", 5, "seconds")["definition_version"] == 1


def test_non_finite_values_are_rejected_even_for_unregistered_metrics():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            validate_sample("anything", value, "x")


def test_unregistered_metrics_are_accepted_but_tagged(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    store.record_metric(day_index=0, metric_name="task_success_rate", value=75, unit="percent")
    store.record_metric(day_index=0, metric_name="test_pass_rate", value=100, unit="percent")
    rows = {m["metric_name"]: json.loads(m["context_json"]) for m in store.list_metrics()}
    assert rows["task_success_rate"]["unregistered_metric"] is True
    assert rows["test_pass_rate"]["definition_version"] == 1
    assert "unregistered_metric" not in rows["test_pass_rate"]


def test_store_refuses_a_known_metric_with_the_wrong_unit(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    with pytest.raises(ValueError):
        store.record_metric(day_index=0, metric_name="fixed_benchmark_duration_seconds",
                            value=5, unit="percent")
    assert store.list_metrics() == []


def test_metric_samples_are_immutable(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    sample_id = store.record_metric(day_index=0, metric_name="cpu_percent", value=10, unit="percent")
    with sqlite3.connect(store.database_path) as db:
        for statement in ("UPDATE metric_samples SET value = 99", "DELETE FROM metric_samples"):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                db.execute(statement)
    assert store.list_metrics()[0]["sample_id"] == sample_id and store.list_metrics()[0]["value"] == 10


def test_snapshot_records_duration_in_seconds_not_percent(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    ExperimentSnapshotCapturer(observatory=store, baseline_capture=lambda root: passing_manifest(),
                               resource_monitor=FakeResources(), repo_root=tmp_path).capture(experiment_id)
    metrics = {m["metric_name"]: m for m in store.list_metrics()}
    assert metrics["fixed_benchmark_duration_seconds"]["unit"] == "seconds"
    assert metrics["test_pass_rate"]["unit"] == "percent"


def test_every_definition_is_self_consistent():
    for name, d in METRIC_DEFINITIONS.items():
        assert d.name == name and d.unit and d.description and d.sampling and d.source
        assert d.minimum is None or d.maximum is None or d.minimum <= d.maximum
