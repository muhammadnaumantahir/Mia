"""Attribution is correlation only and says what it cannot attribute (MIA-OBS-04)."""

import pytest

from mia.observatory.attribution import build_attribution
from mia.observatory.store import ObservatoryStore
from tests.test_experiment_scheduler import passing_manifest

BASE = passing_manifest()["source_commit"]


def _experiment(tmp_path, days=5):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    experiment_id = store.prepare_experiment(
        name="t", baseline_manifest=passing_manifest(), planned_days=days)["experiment_id"]
    store.start_experiment(experiment_id)
    return store, experiment_id


def _day(store, experiment_id, day, *, commit=BASE, transitions=None, cap_available=True, **metrics):
    activity = {"capabilities": {"available": cap_available,
                                 "transitions": transitions or {}, "owner_approved_actions": 0}}
    store.record_daily_snapshot(experiment_id=experiment_id, day_index=day, source_commit=commit,
                                snapshot={"day_index": day, "activity": activity})
    for name, value in metrics.items():
        store.record_metric(day_index=day, metric_name=name, value=value, unit="percent",
                            experiment_id=experiment_id)


def _rows(report, metric):
    return report["metrics"][metric]


def test_a_metric_change_with_no_recorded_change_is_still_not_explained(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, test_pass_rate=100.0)
    _day(store, eid, 2, test_pass_rate=90.0)
    (row,) = _rows(build_attribution(store, eid), "test_pass_rate")
    assert row["delta"] == -10.0 and row["status"] == "no_recorded_change_in_window"
    assert row["candidate_changes"] == [] and row["causal_claim_allowed"] is False


def test_a_commit_change_is_a_single_candidate_never_a_cause(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, test_pass_rate=100.0)
    _day(store, eid, 2, commit="newcommit", test_pass_rate=95.0)
    (row,) = _rows(build_attribution(store, eid), "test_pass_rate")
    assert row["status"] == "single_candidate_correlation_only"
    assert row["candidate_changes"][0]["type"] == "commit"
    assert row["candidate_changes"][0]["from"] == BASE and row["candidate_changes"][0]["to"] == "newcommit"
    assert row["causal_claim_allowed"] is False and "controlled comparison" in row["causal_reason"]


def test_simultaneous_changes_are_reported_as_confounded(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=20.0)
    _day(store, eid, 2, commit="c2", transitions={"active": 1}, cpu_percent=35.0)
    (row,) = _rows(build_attribution(store, eid), "cpu_percent")
    assert row["status"] == "confounded_multiple_candidates"
    assert row["candidate_types"] == ["capability_transitions", "commit"]


def test_an_unavailable_capability_source_is_not_treated_as_no_change(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=20.0)
    _day(store, eid, 2, transitions={"active": 3}, cap_available=False, cpu_percent=25.0)
    (row,) = _rows(build_attribution(store, eid), "cpu_percent")
    assert row["candidate_types"] == []  # unknown source contributes no claim either way


def test_days_without_evidence_inside_a_window_are_listed(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, memory_percent=40.0)
    _day(store, eid, 4, commit="c9", memory_percent=50.0)
    report = build_attribution(store, eid)
    (row,) = _rows(report, "memory_percent")
    assert row["days_without_evidence_inside_window"] == [2, 3]
    assert row["status"] == "single_candidate_correlation_only"


def test_metric_days_beyond_the_recorded_evidence_are_not_attributed(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    store.record_metric(day_index=3, metric_name="cpu_percent", value=30.0, unit="percent",
                        experiment_id=eid)  # no snapshot evidence for day 3
    (row,) = _rows(build_attribution(store, eid), "cpu_percent")
    assert row["status"] == "window_not_covered_by_evidence" and row["candidate_changes"] == []


def test_what_cannot_be_attributed_is_always_stated(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    report = build_attribution(store, eid)
    assert set(report["not_attributable"]) == {"model_or_provider", "dependency_versions"}
    assert report["unobserved_factors"] and "not causes" in report["note"]


def test_other_experiments_and_unknown_ids_are_handled(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, cpu_percent=11.0)
    store.record_metric(day_index=1, metric_name="cpu_percent", value=99.0, unit="percent",
                        experiment_id="someone-else")
    store.record_metric(day_index=2, metric_name="cpu_percent", value=98.0, unit="percent")
    (row,) = _rows(build_attribution(store, eid, ["cpu_percent"]), "cpu_percent")
    assert (row["from_value"], row["to_value"]) == (10.0, 11.0)
    with pytest.raises(ValueError, match="baseline"):
        build_attribution(store, "missing")


# ---- closing OBS-04: dependency and model/provider evidence ------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from mia.gateway.ledger import UsageLedger  # noqa: E402


def _rich_day(store, eid, day, *, packages, models, commit=BASE, **metrics):
    activity = {"capabilities": {"available": True, "transitions": {}, "owner_approved_actions": 0},
                "inference_usage": {"available": True, "requests": 3, "models": [
                    {"provider_id": p, "model_id": m, "requests": 1} for p, m in models]}}
    store.record_daily_snapshot(experiment_id=eid, day_index=day, source_commit=commit, snapshot={
        "day_index": day, "source_commit": commit, "activity": activity,
        "dependencies": {"available": True, "packages": packages}})
    for name, value in metrics.items():
        store.record_metric(day_index=day, metric_name=name, value=value, unit="percent",
                            experiment_id=eid)


def test_dependency_version_changes_are_candidates_and_unchanged_ones_are_not(tmp_path):
    store, eid = _experiment(tmp_path)
    _rich_day(store, eid, 1, packages={"fastapi": "0.1", "ruff": "1.0"}, models=[("o", "a")],
              cpu_percent=10.0)
    _rich_day(store, eid, 2, packages={"fastapi": "0.2", "ruff": "1.0"}, models=[("o", "a")],
              cpu_percent=12.0)
    _rich_day(store, eid, 3, packages={"fastapi": "0.2", "ruff": "1.0"}, models=[("o", "a")],
              cpu_percent=12.0)
    rows = _rows(build_attribution(store, eid), "cpu_percent")
    assert rows[0]["candidate_changes"] == [
        {"type": "dependency_versions", "changed": {"fastapi": ["0.1", "0.2"]}, "window": [1, 2]}]
    assert rows[1]["status"] == "no_recorded_change_in_window"
    assert rows[1]["unknown_factors_in_window"] == []


def test_a_change_in_models_seen_serving_requests_is_a_candidate(tmp_path):
    store, eid = _experiment(tmp_path)
    _rich_day(store, eid, 1, packages={}, models=[("ollama", "a")], cpu_percent=10.0)
    _rich_day(store, eid, 2, packages={}, models=[("ollama", "b"), ("ollama", "a")],
              cpu_percent=30.0)
    (row,) = _rows(build_attribution(store, eid), "cpu_percent")
    (change,) = row["candidate_changes"]
    assert change["type"] == "models_used" and change["added"] == ["ollama/b"]
    assert change["removed"] == [] and row["status"] == "single_candidate_correlation_only"


def test_missing_evidence_is_unknown_for_that_window_not_unchanged(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)           # old-style snapshot: no models or dependencies
    _rich_day(store, eid, 2, packages={"x": "1"}, models=[("o", "a")], cpu_percent=11.0)
    report = build_attribution(store, eid)
    (row,) = _rows(report, "cpu_percent")
    assert row["unknown_factors_in_window"] == ["dependency_versions", "model_or_provider"]
    assert set(report["not_attributable"]) == {"dependency_versions", "model_or_provider"}
    assert "windows: [1, 2]" in report["not_attributable"]["model_or_provider"]


def test_evidence_on_both_sides_removes_the_factor_from_not_attributable(tmp_path):
    store, eid = _experiment(tmp_path)
    _rich_day(store, eid, 1, packages={"x": "1"}, models=[("o", "a")], cpu_percent=10.0)
    _rich_day(store, eid, 2, packages={"x": "1"}, models=[("o", "a")], cpu_percent=11.0)
    report = build_attribution(store, eid)
    # only the first window (Day 0 -> 1) lacks a baseline usage window / baseline dependencies
    assert "[1]" in report["not_attributable"]["model_or_provider"]
    assert "[2]" not in report["not_attributable"]["model_or_provider"]
    assert "[2]" not in report["not_attributable"]["dependency_versions"]


def test_usage_ledger_window_summary_lists_models_that_served_requests(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    for provider, model in (("ollama", "b"), ("ollama", "a"), ("ollama", "a")):
        ledger.record(provider_id=provider, model_id=model, task_type="chat", input_tokens=1,
                      output_tokens=1, estimated_input_tokens=1, estimated_output_tokens=1,
                      cost_usd=0.0, free_tier=True, data_local=True)
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert ledger.window_summary(since, until)["models"] == [
        {"provider_id": "ollama", "model_id": "a", "requests": 2},
        {"provider_id": "ollama", "model_id": "b", "requests": 1}]
    assert ledger.window_summary(until, until)["models"] == []
