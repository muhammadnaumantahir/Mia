"""Reproducible experiment report: sample sizes, confounders, gaps, no causal claims (MIA-OBS-07)."""

from mia.observatory.benchmarks import record_run
from mia.observatory.report_generator import generate_report, render_markdown, verify_report
from mia.observatory.store import ObservatoryStore
from tests.test_experiment_scheduler import passing_manifest

BASE = passing_manifest()["source_commit"]


def _experiment(tmp_path, days=5):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    eid = store.prepare_experiment(name="Test run", baseline_manifest=passing_manifest(),
                                   planned_days=days)["experiment_id"]
    store.start_experiment(eid)
    return store, eid


def _day(store, eid, day, *, commit=BASE, transitions=None, missing=(), cap_available=True,
         **metrics):
    activity = {"capabilities": {"available": cap_available, "transitions": transitions or {},
                                 "owner_approved_actions": 0}}
    store.record_daily_snapshot(experiment_id=eid, day_index=day, source_commit=commit, snapshot={
        "day_index": day, "source_commit": commit, "activity": activity,
        "missing_day_indices": list(missing)})
    for name, value in metrics.items():
        store.record_metric(day_index=day, metric_name=name, value=value, unit="percent",
                            experiment_id=eid)


def _run(passed, total, items=None):
    items = items or {f"s{i}": i < passed for i in range(total)}
    return {"suite_id": "mia-scripted-memory", "suite_version": "sha256:v1",
            "evidence": "deterministic_scripted_provider",
            "conditions": {"models": [], "commit": BASE}, "passed": passed, "total": total,
            "items": items}


def test_unknown_experiment_has_no_report(tmp_path):
    store, _ = _experiment(tmp_path)
    assert generate_report(store, "missing") is None


def test_same_evidence_gives_the_same_report_and_new_evidence_changes_it(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    first, second = generate_report(store, eid), generate_report(store, eid)
    assert first == second and verify_report(store, eid, first)
    assert render_markdown(first) == render_markdown(second)
    store.record_metric(day_index=1, metric_name="memory_percent", value=40.0, unit="percent",
                        experiment_id=eid)
    assert not verify_report(store, eid, first)
    assert generate_report(store, eid)["report_sha256"] != first["report_sha256"]


def test_generating_a_report_writes_nothing(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    before = (len(store.list_events(limit=1000)), len(store.list_metrics()))
    generate_report(store, eid)
    assert (len(store.list_events(limit=1000)), len(store.list_metrics())) == before


def test_coverage_lists_missing_days_without_inventing_them(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, cpu_percent=11.0, missing=[])
    cov = generate_report(store, eid)["coverage"]
    assert (cov["planned_days"], cov["captured_count"], cov["captured_days"]) == (5, 2, [1, 2])
    assert cov["missing_days"] == [3, 4, 5] and cov["unaccounted_days"] == [3, 4, 5]


def test_sample_sizes_flag_small_samples(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, cpu_percent=12.0)
    record_run(store, _run(8, 10), day_index=1, experiment_id=eid)
    sizes = generate_report(store, eid)["sample_sizes"]
    assert sizes["metrics"]["cpu_percent"] == {"samples": 2, "days": [1, 2], "small_sample": True}
    assert sizes["benchmarks"]["mia-scripted-memory"]["small_sample"] is True
    assert sizes["daily_snapshots"] == 2 and sizes["small_sample_threshold"] == 30


def test_benchmark_runs_are_compared_only_when_there_are_two(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    record_run(store, _run(100, 100), day_index=1, experiment_id=eid)
    only = generate_report(store, eid)["benchmarks"]["mia-scripted-memory"]
    assert only["first_vs_last"] == {"comparable": False, "reasons": ["only one run recorded"]}
    _day(store, eid, 2, cpu_percent=11.0)
    record_run(store, _run(60, 100), day_index=2, experiment_id=eid)
    both = generate_report(store, eid)["benchmarks"]["mia-scripted-memory"]
    assert both["first_vs_last"]["comparable"] and both["first_vs_last"]["verdict"] == "lower"
    assert [r["passed"] for r in both["runs"]] == [100, 60]


def test_rollback_days_and_confounders_come_from_recorded_evidence(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, commit="c2", transitions={"rolled_back": 2, "active": 1}, cpu_percent=20.0)
    report = generate_report(store, eid)
    assert report["rollbacks"]["days_with_rollbacks"] == [
        {"day_index": 2, "rolled_back_transitions": 2}]
    text = " ".join(report["confounders"])
    assert "new commit" in text and "[2]" in text and "Capability status transitions" in text
    assert "Days without a snapshot" in text and "Not comparable in some windows" in text


def test_quiet_experiment_has_no_rollbacks_or_change_confounders(tmp_path):
    store, eid = _experiment(tmp_path, days=1)
    _day(store, eid, 1, cpu_percent=10.0)
    report = generate_report(store, eid)
    assert report["rollbacks"]["days_with_rollbacks"] == []
    assert not any("new commit" in c or "Capability status" in c for c in report["confounders"])


def test_markdown_states_limits_and_never_asserts_causation(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, commit="c2", cpu_percent=30.0)
    report = generate_report(store, eid)
    md = render_markdown(report)
    assert report["report_sha256"] in md and "(small sample)" in md
    assert "correlation only" in md and "single_candidate_correlation_only" in md
    assert "## Limitations" in md and "## Confounders" in md and "## Not attributable" in md
    assert "Mia improved because of any change" in report["claims_not_allowed"]
    assert all(r["causal_claim_allowed"] is False
               for rows in report["metric_changes"].values() for r in rows)


def test_an_unavailable_capability_source_never_produces_a_rollback_claim(tmp_path):
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, transitions={"rolled_back": 3}, cap_available=False, cpu_percent=10.0)
    assert generate_report(store, eid)["rollbacks"]["days_with_rollbacks"] == []


def test_timing_runs_appear_in_the_report_and_are_compared_only_when_comparable(tmp_path):
    from mia.brain.evaluation import BUILTIN_SCENARIOS
    from mia.observatory.benchmarks import record_timing_run, run_timing_benchmark
    store, eid = _experiment(tmp_path)
    _day(store, eid, 1, cpu_percent=10.0)
    record_timing_run(store, run_timing_benchmark(BUILTIN_SCENARIOS[:2], repeats=2),
                      day_index=1, experiment_id=eid)
    one = generate_report(store, eid)
    assert one["timing"]["first_vs_last"]["reasons"] == ["fewer than two timing runs"]
    assert one["sample_sizes"]["timing_runs"] == 1
    _day(store, eid, 2, cpu_percent=11.0)
    record_timing_run(store, run_timing_benchmark(BUILTIN_SCENARIOS[:2], repeats=2),
                      day_index=2, experiment_id=eid)
    two = generate_report(store, eid)
    assert len(two["timing"]["runs"]) == 2 and two["timing"]["first_vs_last"]["comparable"]
    assert "pipeline timing: 2 run(s)" in render_markdown(two)
