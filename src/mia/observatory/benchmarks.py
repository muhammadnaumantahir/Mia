"""Versioned benchmark suites, immutable run records and honest regression comparison (MIA-OBS-05).

First slice: the deterministic scripted scenarios of the M1 evaluation, grouped into benchmark
families. A suite's version is a hash of its scenario definitions, so changing a scenario makes a
*new* version and old results are never compared with it. Runs are stored through the observatory
store (immutable events plus a ``benchmark_pass_rate`` metric sample).

What this measures: whether Mia's own pipeline (context assembly, verifier, routing, tool policy)
behaves as scripted. It says nothing about the quality of any live model, and a difference between
two runs is a correlation with whatever changed between them, not proof of its cause.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mia.brain.evaluation import BUILTIN_SCENARIOS, Scenario, run_scenario

from .store import ObservatoryStore

BENCHMARK_SCHEMA_VERSION = 1
EVIDENCE = "deterministic_scripted_provider"
SMALL_SAMPLE = 30

FAMILY_OF_CATEGORY = {
    "continuity": "conversation", "ambiguity": "conversation", "corrections": "conversation",
    "identity": "conversation", "capability-inventory": "conversation",
    "provider-switch": "conversation",
    "memory": "memory", "memory-conflict": "memory",
    "unsupported-claims": "reasoning", "uncertainty": "reasoning",
    "tool-failure": "tools", "tool-safety": "safety",
    "provider-failure": "gateway", "judges": "gateway", "baseline": "gateway",
}

# Deterministic test files used as versioned benchmark suites (browser/Chromium tests excluded).
PYTEST_SUITES: dict[str, tuple[str, ...]] = {
    "voice": ("tests/test_voice_stt.py", "tests/test_voice_tts.py", "tests/test_voice_api.py",
              "tests/test_voice_language_latency.py", "tests/test_voice_language_selection.py",
              "tests/test_voice_wake.py", "tests/test_voice_wake_wiring.py",
              "tests/test_assistant_voice_language.py"),
    "cost": ("tests/test_gateway_budget.py",),
}

# Families the roadmap names that have no automated benchmark, with the honest reason.
NOT_YET_VERSIONED = {
    "live_model_quality": "needs the owner's real models: run `python -m mia.brain.live_eval` "
                          "(a five-probe smoke check, owner-run by design)",
}

SUITE_NOTES = {
    "voice": "server-side voice/STT/TTS/wake behaviour with fakes; no real microphone or speech",
    "cost": "cost-cap and budget accounting correctness with fakes; not real spending",
}
TIMING_SUITE_ID = "mia-scripted-pipeline-timing"
TIMING_EVIDENCE = "timing_scripted_workload"
TIMING_TOLERANCE = 0.25


def _stable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _stable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_stable(v) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return getattr(value, "__qualname__", type(value).__name__)  # callables, objects: by name


def suite_version(scenarios: list[Scenario]) -> str:
    """Content hash of the scenario definitions; any edit yields a different version."""
    payload = json.dumps([_stable(s) for s in sorted(scenarios, key=lambda s: s.name)],
                         sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def families(scenarios: tuple[Scenario, ...] = BUILTIN_SCENARIOS) -> dict[str, list[Scenario]]:
    grouped: dict[str, list[Scenario]] = {}
    for scenario in scenarios:
        family = FAMILY_OF_CATEGORY.get(scenario.category)
        if family is None:
            raise ValueError(f"scenario category {scenario.category!r} has no benchmark family")
        grouped.setdefault(family, []).append(scenario)
    return dict(sorted(grouped.items()))


def wilson_interval(passed: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a pass rate, as fractions in [0, 1]."""
    if total <= 0:
        raise ValueError("an interval needs at least one item")
    if not 0 <= passed <= total:
        raise ValueError("passed must be between 0 and total")
    p = passed / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


async def run_family(family: str, scenarios: list[Scenario], *, commit: str | None = None
                     ) -> dict[str, Any]:
    results = [await run_scenario(s) for s in scenarios]
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "suite_id": f"mia-scripted-{family}",
        "suite_version": suite_version(scenarios),
        "family": family,
        "evidence": EVIDENCE,
        "conditions": {"models": [], "commit": commit},
        "passed": sum(r.passed for r in results),
        "total": len(results),
        "items": {r.name: bool(r.passed) for r in results},
    }


def run_all_families(scenarios: tuple[Scenario, ...] = BUILTIN_SCENARIOS, *,
                     commit: str | None = None) -> list[dict[str, Any]]:
    async def go() -> list[dict[str, Any]]:
        return [await run_family(f, s, commit=commit) for f, s in families(scenarios).items()]
    return asyncio.run(go())


def record_run(store: ObservatoryStore, run: dict[str, Any], *, day_index: int = 0,
               experiment_id: str | None = None) -> str:
    """Persist one run as an immutable event plus a pass-rate sample; returns the event id."""
    if run["total"] <= 0:
        raise ValueError("a run with no items has no pass rate; nothing is recorded")
    event_id = store.record_event(
        category="benchmark", event_type="benchmark_run",
        summary=f"{run['suite_id']}: {run['passed']}/{run['total']} passed",
        outcome="recorded", day_index=day_index, experiment_id=experiment_id,
        layer_id="SPS-09", payload=run)
    store.record_metric(
        day_index=day_index, metric_name="benchmark_pass_rate",
        value=run["passed"] / run["total"] * 100.0, unit="percent",
        benchmark_id=run["suite_id"], experiment_id=experiment_id, source_event_id=event_id,
        context={"suite_version": run["suite_version"], "passed": run["passed"],
                 "total": run["total"], "evidence": run["evidence"]})
    return event_id


def list_runs(store: ObservatoryStore, suite_id: str) -> list[dict[str, Any]]:
    """Recorded runs of one suite, oldest first."""
    runs = []
    for event in reversed(store.list_events(limit=1000)):
        if event["event_type"] != "benchmark_run":
            continue
        payload = json.loads(event["payload_json"])
        if payload.get("suite_id") == suite_id:
            runs.append({**payload, "event_id": event["event_id"],
                         "occurred_at": event["occurred_at"]})
    return runs


def compare_runs(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Compare two runs only if they were measured under equivalent conditions."""
    reasons = []
    for key, label in (("suite_id", "suite"), ("suite_version", "suite version"),
                       ("evidence", "kind of evidence")):
        if baseline.get(key) != candidate.get(key):
            reasons.append(f"different {label}")
    if sorted(baseline["conditions"].get("models", [])) != sorted(
            candidate["conditions"].get("models", [])):
        reasons.append("different models")
    if reasons:
        return {"comparable": False, "reasons": reasons,
                "note": "Runs under different conditions are never compared."}

    b_lo, b_hi = wilson_interval(baseline["passed"], baseline["total"])
    c_lo, c_hi = wilson_interval(candidate["passed"], candidate["total"])
    b_rate, c_rate = baseline["passed"] / baseline["total"], candidate["passed"] / candidate["total"]
    if c_hi < b_lo:
        verdict = "lower"
    elif c_lo > b_hi:
        verdict = "higher"
    else:
        verdict = "no clear difference"
    base_items, cand_items = baseline["items"], candidate["items"]
    flips_down = sorted(n for n in base_items if base_items[n] and cand_items.get(n) is False)
    flips_up = sorted(n for n in base_items if base_items[n] is False and cand_items.get(n))
    return {
        "comparable": True,
        "baseline": {"rate": b_rate, "interval95": [b_lo, b_hi], "passed": baseline["passed"],
                     "total": baseline["total"]},
        "candidate": {"rate": c_rate, "interval95": [c_lo, c_hi], "passed": candidate["passed"],
                      "total": candidate["total"]},
        "difference": c_rate - b_rate,
        "verdict": verdict,
        "items_now_failing": flips_down,
        "items_now_passing": flips_up,
        "small_sample": min(baseline["total"], candidate["total"]) < SMALL_SAMPLE,
        "commits": [baseline["conditions"].get("commit"), candidate["conditions"].get("commit")],
        "note": ("A difference is a correlation with whatever changed between these runs "
                 "(code, config, models), not evidence of its cause. Scripted scenarios say "
                 "nothing about live-model quality."),
    }


class BenchmarkError(RuntimeError):
    """A benchmark could not produce a trustworthy result; nothing should be recorded."""


def _files_version(root: Path, files: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        path = root / name
        if not path.is_file():
            raise BenchmarkError(f"benchmark file missing: {name}")
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return "sha256:" + digest.hexdigest()


def _parse_junit(path: Path) -> tuple[dict[str, bool], int]:
    root = ET.parse(path).getroot()
    items: dict[str, bool] = {}
    skipped = 0
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            skipped += 1
            continue
        failed = case.find("failure") is not None or case.find("error") is not None
        items[f"{case.get('classname', '')}::{case.get('name', '')}"] = not failed
    return items, skipped


def run_pytest_suite(family: str, root: Path, *, commit: str | None = None,
                     runner: Callable[..., Any] | None = None,
                     timeout_seconds: int = 900) -> dict[str, Any]:
    """Run a fixed set of deterministic test files as one versioned benchmark suite."""
    files = PYTEST_SUITES[family]
    version = _files_version(root, files)
    runner = runner or subprocess.run
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "junit.xml"
        try:
            runner([sys.executable, "-m", "pytest", *files, "-q", "-p", "no:cacheprovider",
                    f"--junitxml={report}"], cwd=root, capture_output=True, text=True,
                   timeout=timeout_seconds, check=False)
            items, skipped = _parse_junit(report)
        except (OSError, subprocess.TimeoutExpired, ET.ParseError) as exc:
            raise BenchmarkError(f"suite {family} produced no usable result: {type(exc).__name__}"
                                 ) from exc
    if not items:
        raise BenchmarkError(f"suite {family} ran no tests")
    return {"schema_version": BENCHMARK_SCHEMA_VERSION, "suite_id": f"mia-tests-{family}",
            "suite_version": version, "family": family, "evidence": "pytest_suite",
            "note": SUITE_NOTES[family], "conditions": {"models": [], "commit": commit},
            "passed": sum(items.values()), "total": len(items), "skipped": skipped,
            "items": dict(sorted(items.items()))}


# ---- timing and memory of a fixed scripted workload ----------------------------------------

def _environment() -> dict[str, Any]:
    return {"python": platform.python_version(), "platform": platform.platform(),
            "machine": platform.machine(), "cpu_count": os.cpu_count()}


def run_timing_benchmark(scenarios: tuple[Scenario, ...] = BUILTIN_SCENARIOS, *, repeats: int = 5,
                         commit: str | None = None) -> dict[str, Any]:
    """Time the whole scripted workload several times; report the spread, not one number."""
    if repeats < 2:
        raise ValueError("timing needs at least two repeats to show any spread")

    async def workload() -> None:
        for scenario in scenarios:
            await run_scenario(scenario)

    timings, peaks = [], []
    for _ in range(repeats):
        tracemalloc.start()
        started = time.perf_counter()
        asyncio.run(workload())
        timings.append((time.perf_counter() - started) * 1000.0)
        peaks.append(tracemalloc.get_traced_memory()[1] / 1024.0)
        tracemalloc.stop()
    return {"schema_version": BENCHMARK_SCHEMA_VERSION, "suite_id": TIMING_SUITE_ID,
            "suite_version": suite_version(list(scenarios)), "family": "pipeline_timing",
            "evidence": TIMING_EVIDENCE,
            "conditions": {"models": [], "commit": commit, "environment": _environment(),
                           "repeats": repeats},
            "timings_ms": [round(t, 3) for t in timings],
            "peak_python_kib": [round(p, 1) for p in peaks],
            "median_ms": round(statistics.median(timings), 3),
            "min_ms": round(min(timings), 3), "max_ms": round(max(timings), 3),
            "median_peak_python_kib": round(statistics.median(peaks), 1),
            "note": ("Wall time and Python-heap peak of Mia's own scripted pipeline on this host "
                     "(tracemalloc: Python allocations only). Measures pipeline overhead, not "
                     "model latency or cost.")}


def compare_timing(baseline: dict[str, Any], candidate: dict[str, Any],
                   tolerance: float = TIMING_TOLERANCE) -> dict[str, Any]:
    """Timing is noisy: 'slower'/'faster' only when every repeat of one side is beyond the other."""
    reasons = []
    for key, label in (("suite_id", "suite"), ("suite_version", "suite version"),
                       ("evidence", "kind of evidence")):
        if baseline.get(key) != candidate.get(key):
            reasons.append(f"different {label}")
    if baseline["conditions"].get("environment") != candidate["conditions"].get("environment"):
        reasons.append("different host or Python environment")
    if baseline["conditions"].get("repeats") != candidate["conditions"].get("repeats"):
        reasons.append("different number of repeats")
    if reasons:
        return {"comparable": False, "reasons": reasons,
                "note": "Timings under different conditions are never compared."}
    if candidate["min_ms"] > baseline["max_ms"] * (1 + tolerance):
        verdict = "slower"
    elif candidate["max_ms"] * (1 + tolerance) < baseline["min_ms"]:
        verdict = "faster"
    else:
        verdict = "no clear difference"
    return {"comparable": True, "verdict": verdict, "tolerance": tolerance,
            "baseline_range_ms": [baseline["min_ms"], baseline["max_ms"]],
            "candidate_range_ms": [candidate["min_ms"], candidate["max_ms"]],
            "median_ratio": round(candidate["median_ms"] / baseline["median_ms"], 3),
            "peak_python_kib": [baseline["median_peak_python_kib"],
                                candidate["median_peak_python_kib"]],
            "note": ("Other load on the host affects wall time. A difference is a correlation "
                     "with what changed between runs, not proof of its cause.")}


def record_timing_run(store: ObservatoryStore, run: dict[str, Any], *, day_index: int = 0,
                      experiment_id: str | None = None) -> str:
    event_id = store.record_event(
        category="benchmark", event_type="benchmark_timing_run",
        summary=f"{run['suite_id']}: median {run['median_ms']:g} ms over "
                f"{run['conditions']['repeats']} runs",
        outcome="recorded", day_index=day_index, experiment_id=experiment_id,
        layer_id="SPS-09", payload=run)
    for name, value, unit in (("pipeline_median_ms", run["median_ms"], "ms"),
                              ("pipeline_peak_python_kib", run["median_peak_python_kib"], "kib")):
        store.record_metric(day_index=day_index, metric_name=name, value=value, unit=unit,
                            benchmark_id=run["suite_id"], experiment_id=experiment_id,
                            source_event_id=event_id,
                            context={"suite_version": run["suite_version"],
                                     "repeats": run["conditions"]["repeats"]})
    return event_id
