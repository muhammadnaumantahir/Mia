"""Reproducible Day 0-30 experiment report (MIA-OBS-07).

Composes the integrity-checked ``build_experiment_report`` with change attribution and recorded
benchmark runs. The same stored evidence always yields the same report and the same
``report_sha256`` (no generation time is mixed in), so a reader can regenerate and compare.

The report states observations and recorded changes. It never says Mia "improved" or that a
change "caused" a result; every gap, confounder and limitation is listed explicitly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .attribution import build_attribution
from .benchmarks import SMALL_SAMPLE, compare_runs
from .store import ObservatoryStore

REPORT_SCHEMA_VERSION = 1

LIMITATIONS = (
    "Scripted benchmark results describe Mia's own pipeline, not the quality of any live model.",
    "Metric changes are correlated with recorded changes only; no causal claim is made.",
    "Capability registry counts are window counts, not linked to this experiment's ID.",
    "Model/provider and dependency-version changes are not captured per snapshot, so they cannot "
    "be ruled in or out as explanations.",
    "One host, one owner; nothing here generalises to other machines or users.",
)
CLAIMS_NOT_ALLOWED = ("Mia improved because of any change", "any change caused a metric to move",
                      "results hold for live models")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sample_sizes(base: dict[str, Any], benchmark_runs: dict[str, list[dict[str, Any]]]) -> dict:
    metrics: dict[str, Any] = {}
    for name, series in sorted(base["metric_series"].items()):
        days = sorted({p["day_index"] for p in series})
        metrics[name] = {"samples": len(series), "days": days,
                         "small_sample": len(series) < SMALL_SAMPLE}
    suites = {sid: {"runs": len(runs), "items_in_latest_run": runs[-1]["total"],
                    "small_sample": runs[-1]["total"] < SMALL_SAMPLE}
              for sid, runs in sorted(benchmark_runs.items())}
    return {"daily_snapshots": len([s for s in base["snapshots"] if s["day_index"] > 0]),
            "planned_days": base["experiment"]["planned_days"], "metrics": metrics,
            "benchmarks": suites, "small_sample_threshold": SMALL_SAMPLE}


def _benchmarks(base: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    runs: dict[str, list[dict[str, Any]]] = {}
    for event in base["events"]:
        if event["event_type"] == "benchmark_run":
            runs.setdefault(event["payload"]["suite_id"], []).append(
                {**event["payload"], "event_id": event["event_id"], "day_index": event["day_index"]})
    return runs


def _benchmark_section(runs: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    section: dict[str, Any] = {}
    for suite_id, items in sorted(runs.items()):
        first, last = items[0], items[-1]
        section[suite_id] = {
            "runs": [{"event_id": r["event_id"], "day_index": r["day_index"],
                      "suite_version": r["suite_version"], "passed": r["passed"],
                      "total": r["total"], "commit": r["conditions"].get("commit")} for r in items],
            "first_vs_last": (compare_runs(first, last) if len(items) > 1 else
                              {"comparable": False, "reasons": ["only one run recorded"]}),
        }
    return section


def _rollbacks(base: dict[str, Any]) -> dict[str, Any]:
    days = []
    for snap in base["snapshots"]:
        caps = (snap["evidence"].get("activity") or {}).get("capabilities") or {}
        count = (caps.get("transitions") or {}).get("rolled_back", 0) if caps.get("available") else 0
        if count:
            days.append({"day_index": snap["day_index"], "rolled_back_transitions": count})
    return {"days_with_rollbacks": days,
            "note": "Counted from per-window capability transition counts; versions are not "
                    "linked to this experiment."}


def _confounders(base: dict[str, Any], attribution: dict[str, Any]) -> list[str]:
    found = []
    commit_days = sorted({w["to_day"] for w in attribution["windows"]
                          if any(c["type"] == "commit" for c in w["changes"])})
    if commit_days:
        found.append(f"Code changed (new commit) before days {commit_days}.")
    cap_days = sorted({w["to_day"] for w in attribution["windows"]
                       if any(c["type"] == "capability_transitions" for c in w["changes"])})
    if cap_days:
        found.append(f"Capability status transitions occurred in the windows ending days {cap_days}.")
    days = base["days"]
    if days["missing"]:
        found.append(f"Days without a snapshot: {days['missing']} "
                     f"(of which unaccounted for: {days['unaccounted']}).")
    counts = {e["event_type"] for e in base["events"]}
    for event_type, text in (
            ("daily_snapshot_deferred_resource_pressure", "Some captures were deferred by host resource pressure."),
            ("daily_snapshot_auto_capture_failed", "Some automatic captures failed.")):
        if event_type in counts:
            found.append(text)
    found.append("Model/provider and dependency versions were not recorded per snapshot.")
    found.append("The mix of conversations and tasks each day was not controlled.")
    return found


def generate_report(store: ObservatoryStore, experiment_id: str) -> dict[str, Any] | None:
    base = store.build_experiment_report(experiment_id)
    if base is None:
        return None
    attribution = build_attribution(store, experiment_id)
    runs = _benchmarks(base)
    captured = [d for d in base["days"]["recorded"] if d > 0]
    body = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "overview": {**base["experiment"], "report_ready": base["report_ready"],
                     "baseline_sha256": base["baseline"]["sha256"],
                     "comparison_is_final": base["comparison"]["comparison_is_final"]},
        "coverage": {"planned_days": base["experiment"]["planned_days"],
                     "captured_days": captured, "missing_days": base["days"]["missing"],
                     "unavailable_days": base["days"]["unavailable"],
                     "unaccounted_days": base["days"]["unaccounted"],
                     "captured_count": len(captured)},
        "sample_sizes": _sample_sizes(base, runs),
        "metric_changes": attribution["metrics"],
        "attribution_limits": {"not_attributable": attribution["not_attributable"],
                               "unobserved_factors": attribution["unobserved_factors"]},
        "benchmarks": _benchmark_section(runs),
        "rollbacks": _rollbacks(base),
        "confounders": _confounders(base, attribution),
        "limitations": list(LIMITATIONS),
        "claims_not_allowed": list(CLAIMS_NOT_ALLOWED),
        "evidence": {"snapshot_manifest_sha256": {str(s["day_index"]): s["manifest_sha256"]
                                                    for s in base["snapshots"]},
                     "event_count": base["integrity"]["event_count"],
                     "metric_count": base["integrity"]["metric_count"]},
    }
    return {**body, "report_sha256": hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()}


def verify_report(store: ObservatoryStore, experiment_id: str, report: dict[str, Any]) -> bool:
    """True when regenerating from the stored evidence gives an identical report."""
    fresh = generate_report(store, experiment_id)
    return fresh is not None and fresh["report_sha256"] == report.get("report_sha256")


def render_markdown(report: dict[str, Any]) -> str:
    o, c, s = report["overview"], report["coverage"], report["sample_sizes"]
    lines = [f"# Experiment report: {o['name']}", "",
             f"Status: **{o['status']}** (conclusion: {o['conclusion']}); "
             f"report ready: {o['report_ready']}; baseline `{o['baseline_sha256'][:12]}`.", "",
             "## Coverage", "",
             f"Captured {c['captured_count']} of {c['planned_days']} planned days. "
             f"Missing: {c['missing_days'] or 'none'}; unavailable (Mia offline): "
             f"{c['unavailable_days'] or 'none'}; unaccounted for: {c['unaccounted_days'] or 'none'}.",
             "", "## Sample sizes", ""]
    for name, info in s["metrics"].items():
        flag = " (small sample)" if info["small_sample"] else ""
        lines.append(f"- {name}: {info['samples']} samples on days {info['days']}{flag}")
    for suite, info in s["benchmarks"].items():
        flag = " (small sample)" if info["small_sample"] else ""
        lines.append(f"- benchmark {suite}: {info['runs']} runs, {info['items_in_latest_run']} items{flag}")
    lines += ["", "## Metric changes (correlation only)", ""]
    for name, rows in report["metric_changes"].items():
        for r in rows:
            kinds = ", ".join(r["candidate_types"]) or "none recorded"
            lines.append(f"- {name} day {r['from_day']}->{r['to_day']}: "
                         f"{r['from_value']:g} -> {r['to_value']:g} ({r['delta']:+g}); "
                         f"{r['status']}; candidates: {kinds}")
    lines += ["", "## Benchmarks", ""]
    for suite, info in report["benchmarks"].items():
        cmp = info["first_vs_last"]
        verdict = cmp.get("verdict") if cmp.get("comparable") else "not comparable: " + "; ".join(
            cmp["reasons"])
        lines.append(f"- {suite}: {len(info['runs'])} run(s); first vs last: {verdict}")
    if not report["benchmarks"]:
        lines.append("- no benchmark runs recorded")
    rb = report["rollbacks"]["days_with_rollbacks"]
    lines += ["", "## Rollbacks", "", f"- {rb or 'none recorded'} ({report['rollbacks']['note']})",
              "", "## Confounders", ""] + [f"- {x}" for x in report["confounders"]]
    lines += ["", "## Not attributable", ""] + [
        f"- {k}: {v}" for k, v in report["attribution_limits"]["not_attributable"].items()]
    lines += ["", "## Limitations", ""] + [f"- {x}" for x in report["limitations"]]
    lines += ["", "## Claims this report does not make", ""] + [
        f"- {x}" for x in report["claims_not_allowed"]]
    lines += ["", f"Report hash: `{report['report_sha256']}`", ""]
    return "\n".join(lines)
