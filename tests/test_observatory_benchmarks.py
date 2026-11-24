"""Versioned benchmarks, run records and honest comparison (MIA-OBS-05). Scripted evidence only."""

import copy
import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from mia.brain.evaluation import BUILTIN_SCENARIOS
from mia.observatory.benchmarks import (
    NOT_YET_VERSIONED,
    PYTEST_SUITES,
    BenchmarkError,
    compare_runs,
    compare_timing,
    families,
    list_runs,
    record_run,
    record_timing_run,
    run_all_families,
    run_pytest_suite,
    run_timing_benchmark,
    suite_version,
    wilson_interval,
)
from mia.observatory.store import ObservatoryStore


def _run(passed, total, *, version="sha256:v1", models=(), evidence="deterministic_scripted_provider",
         items=None, commit="c1", suite="mia-scripted-memory"):
    items = items or {f"s{i}": i < passed for i in range(total)}
    return {"suite_id": suite, "suite_version": version, "evidence": evidence,
            "conditions": {"models": list(models), "commit": commit},
            "passed": passed, "total": total, "items": items}


def test_wilson_interval_matches_known_values():
    lo, hi = wilson_interval(5, 10)
    assert (round(lo, 3), round(hi, 3)) == (0.237, 0.763)
    assert wilson_interval(0, 10)[0] == 0.0 and round(wilson_interval(0, 10)[1], 3) == 0.278
    assert round(wilson_interval(10, 10)[0], 3) == 0.722 and wilson_interval(10, 10)[1] == 1.0
    for bad in ((0, 0), (11, 10), (-1, 5)):
        with pytest.raises(ValueError):
            wilson_interval(*bad)


def test_every_builtin_scenario_belongs_to_a_family_and_the_scripted_suites_pass():
    fams = families()
    assert sum(len(v) for v in fams.values()) == len(BUILTIN_SCENARIOS)
    runs = run_all_families(commit="abc")
    assert {r["family"] for r in runs} == set(fams)
    assert [(r["suite_id"], r["passed"], r["total"]) for r in runs if r["passed"] != r["total"]] == []


def test_unknown_category_is_refused_rather_than_silently_dropped():
    odd = dataclasses.replace(BUILTIN_SCENARIOS[0], name="odd", category="brand-new")
    with pytest.raises(ValueError, match="brand-new"):
        families((odd,))


def test_suite_version_is_stable_across_processes_and_changes_with_any_edit():
    memory = families()["memory"]
    code = ("from mia.observatory.benchmarks import families, suite_version;"
            "print(suite_version(families()['memory']))")
    other = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           check=True).stdout.strip()
    assert suite_version(memory) == other
    edited = [dataclasses.replace(memory[0], prompt=memory[0].prompt + " (edited)"), *memory[1:]]
    assert suite_version(edited) != suite_version(memory)
    assert suite_version(list(reversed(memory))) == suite_version(memory)  # order never matters


def test_runs_are_recorded_immutably_with_a_pass_rate_sample(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    first = record_run(store, _run(8, 10))
    second = record_run(store, _run(9, 10, commit="c2"))
    runs = list_runs(store, "mia-scripted-memory")
    assert [r["event_id"] for r in runs] == [first, second]
    assert list_runs(store, "mia-scripted-other") == []
    samples = [m for m in store.list_metrics("benchmark_pass_rate")]
    assert [m["value"] for m in samples] == [80.0, 90.0] and samples[0]["unit"] == "percent"
    with pytest.raises(ValueError):
        record_run(store, _run(0, 0))
    assert len(store.list_metrics("benchmark_pass_rate")) == 2  # nothing recorded for the empty run


def test_runs_under_different_conditions_are_never_compared():
    base = _run(10, 10)
    for other, reason in ((_run(10, 10, version="sha256:v2"), "suite version"),
                          (_run(10, 10, models=["ollama/m"]), "models"),
                          (_run(10, 10, evidence="live_local_model"), "kind of evidence"),
                          (_run(10, 10, suite="mia-scripted-tools"), "suite")):
        result = compare_runs(base, other)
        assert result["comparable"] is False and any(reason in r for r in result["reasons"])
        assert "verdict" not in result


def test_identical_results_show_no_clear_difference_and_small_samples_are_flagged():
    result = compare_runs(_run(8, 10), _run(8, 10, commit="c2"))
    assert result["comparable"] and result["verdict"] == "no clear difference"
    assert result["small_sample"] is True and result["difference"] == 0
    assert result["commits"] == ["c1", "c2"]
    assert "correlation" in result["note"] and "cause" in result["note"]


def test_a_large_drop_is_reported_as_lower_and_a_small_wobble_is_not():
    assert compare_runs(_run(100, 100), _run(60, 100))["verdict"] == "lower"
    assert compare_runs(_run(60, 100), _run(100, 100))["verdict"] == "higher"
    assert compare_runs(_run(10, 10), _run(9, 10))["verdict"] == "no clear difference"  # 1 item
    assert compare_runs(_run(100, 100), _run(100, 100))["small_sample"] is False


def test_items_that_flipped_are_named_even_when_the_overall_rate_hides_it():
    base = _run(2, 2, items={"a": True, "b": True})
    cand = copy.deepcopy(base)
    cand["items"] = {"a": False, "b": True}
    cand["passed"] = 1
    result = compare_runs(base, cand)
    assert result["items_now_failing"] == ["a"] and result["items_now_passing"] == []
    swap = compare_runs(_run(1, 2, items={"a": True, "b": False}),
                        _run(1, 2, items={"a": False, "b": True}))
    assert swap["difference"] == 0
    assert swap["items_now_failing"] == ["a"] and swap["items_now_passing"] == ["b"]


def test_only_live_model_quality_is_left_to_the_owner():
    assert set(NOT_YET_VERSIONED) == {"live_model_quality"}
    assert "live_eval" in NOT_YET_VERSIONED["live_model_quality"]


# ---- closing OBS-05: test-file suites, timing/memory benchmark ----------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _tiny_repo(tmp_path, *, body="def test_a():\n    assert True\n\ndef test_b():\n    assert False\n"):
    (tmp_path / "t.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_every_pytest_suite_file_exists_and_is_not_a_browser_test():
    for family, files in PYTEST_SUITES.items():
        for name in files:
            text = (ROOT / name).read_text(encoding="utf-8")
            assert "chromium" not in text and "sync_playwright" not in text, (family, name)


def test_pytest_suite_runs_real_tests_and_records_per_item_outcomes(tmp_path, monkeypatch):
    repo = _tiny_repo(tmp_path)
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("t.py",))
    run = run_pytest_suite("voice", repo, commit="abc")
    assert (run["passed"], run["total"], run["suite_id"]) == (1, 2, "mia-tests-voice")
    assert sorted(run["items"].values()) == [False, True] and run["conditions"]["commit"] == "abc"


def test_pytest_suite_version_changes_with_the_test_files(tmp_path, monkeypatch):
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("t.py",))
    first = run_pytest_suite("voice", _tiny_repo(tmp_path))["suite_version"]
    (tmp_path / "t.py").write_text("def test_a():\n    assert True\n", encoding="utf-8")
    assert run_pytest_suite("voice", tmp_path)["suite_version"] != first


def test_a_suite_that_cannot_run_records_nothing_instead_of_zero(tmp_path, monkeypatch):
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("t.py",))
    repo = _tiny_repo(tmp_path)

    def crashed(*args, **kwargs):  # never writes the junit file
        return subprocess.CompletedProcess(args, 2, "", "boom")

    with pytest.raises(BenchmarkError):
        run_pytest_suite("voice", repo, runner=crashed)
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("missing.py",))
    with pytest.raises(BenchmarkError, match="missing"):
        run_pytest_suite("voice", repo)
    (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("empty.py",))
    with pytest.raises(BenchmarkError, match="no tests"):
        run_pytest_suite("voice", repo)


def test_pytest_suite_runs_compare_like_any_other_run(tmp_path, monkeypatch):
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("t.py",))
    repo = _tiny_repo(tmp_path)
    a, b = run_pytest_suite("voice", repo), run_pytest_suite("voice", repo)
    assert compare_runs(a, b)["comparable"] and compare_runs(a, b)["verdict"] == "no clear difference"


def _timing(median, lo, hi, *, env="host-a", repeats=5, version="sha256:v"):
    return {"suite_id": "mia-scripted-pipeline-timing", "suite_version": version,
            "evidence": "timing_scripted_workload",
            "conditions": {"models": [], "environment": env, "repeats": repeats},
            "median_ms": median, "min_ms": lo, "max_ms": hi, "median_peak_python_kib": 150.0}


def test_timing_benchmark_reports_the_spread_and_needs_repeats():
    run = run_timing_benchmark(BUILTIN_SCENARIOS[:3], repeats=3, commit="c")
    assert len(run["timings_ms"]) == len(run["peak_python_kib"]) == 3
    assert run["min_ms"] <= run["median_ms"] <= run["max_ms"] and run["median_peak_python_kib"] > 0
    assert run["conditions"]["environment"]["python"] and "not model latency" in run["note"]
    with pytest.raises(ValueError):
        run_timing_benchmark(repeats=1)


def test_timing_is_compared_only_in_equal_environments_and_with_noise_tolerance():
    base = _timing(100, 95, 110)
    assert compare_timing(base, _timing(105, 99, 112))["verdict"] == "no clear difference"
    assert compare_timing(base, _timing(200, 180, 240))["verdict"] == "slower"
    assert compare_timing(_timing(200, 180, 240), base)["verdict"] == "faster"
    overlap = compare_timing(base, _timing(140, 90, 200))  # one repeat as fast as the baseline's
    assert overlap["verdict"] == "no clear difference"
    for other, why in ((_timing(100, 95, 110, env="host-b"), "environment"),
                       (_timing(100, 95, 110, repeats=3), "repeats"),
                       (_timing(100, 95, 110, version="sha256:w"), "suite version")):
        result = compare_timing(base, other)
        assert result["comparable"] is False and why in " ".join(result["reasons"])


def test_timing_runs_are_recorded_with_metrics(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    run = run_timing_benchmark(BUILTIN_SCENARIOS[:2], repeats=2)
    record_timing_run(store, run, day_index=1)
    metrics = {m["metric_name"]: m for m in store.list_metrics()}
    assert metrics["pipeline_median_ms"]["unit"] == "ms"
    assert metrics["pipeline_peak_python_kib"]["unit"] == "kib"


def test_skipped_tests_are_counted_as_skipped_not_as_passes(tmp_path, monkeypatch):
    monkeypatch.setitem(PYTEST_SUITES, "voice", ("t.py",))
    repo = _tiny_repo(tmp_path, body=(
        "import pytest\n\ndef test_a():\n    assert True\n\n"
        "@pytest.mark.skip(reason='x')\ndef test_b():\n    assert False\n"))
    run = run_pytest_suite("voice", repo)
    assert (run["passed"], run["total"], run["skipped"]) == (1, 1, 1)
