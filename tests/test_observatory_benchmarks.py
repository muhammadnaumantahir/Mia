"""Versioned benchmarks, run records and honest comparison (MIA-OBS-05). Scripted evidence only."""

import copy
import dataclasses
import subprocess
import sys

import pytest

from mia.brain.evaluation import BUILTIN_SCENARIOS
from mia.observatory.benchmarks import (
    NOT_YET_VERSIONED,
    compare_runs,
    families,
    list_runs,
    record_run,
    run_all_families,
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


def test_families_without_a_versioned_benchmark_are_listed_with_reasons():
    assert {"voice", "latency", "cost", "resource_use", "live_model_quality"} <= set(NOT_YET_VERSIONED)
    assert all(NOT_YET_VERSIONED.values())
