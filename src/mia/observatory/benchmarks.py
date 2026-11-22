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

# Families the roadmap names that have no versioned benchmark yet, with the honest reason.
NOT_YET_VERSIONED = {
    "voice": "covered by browser tests that pass or fail; no scored, versioned voice benchmark yet",
    "latency": "recorded per daily snapshot (inference_latency_*_ms); no fixed benchmark workload",
    "cost": "recorded per daily snapshot (inference_cost_usd); no fixed benchmark workload",
    "resource_use": "recorded per daily snapshot (cpu_percent, memory_percent); no fixed workload",
    "live_model_quality": "opt-in `python -m mia.brain.live_eval` smoke check only; owner-run",
}


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
