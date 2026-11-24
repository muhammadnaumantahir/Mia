"""Link metric changes to the changes recorded between observations (MIA-OBS-04).

Everything here is *correlation only*. Between two captured days the code may have changed,
capabilities may have transitioned, and usage differed; a metric moved in the same window. That
makes those changes **candidates**, never causes: days are sequential observations of one
system, not a controlled comparison, so ``causal_claim_allowed`` is always false.

Only evidence the stores actually keep is used. Model/provider and dependency changes cannot be
attributed today (the usage summary has no per-model breakdown; snapshots do not store dependency
versions) and are reported as not attributable instead of being silently omitted.
"""

from __future__ import annotations

import json
from typing import Any

from .store import ObservatoryStore

UNKNOWN_REASON = {
    "model_or_provider": "no usage-window model list on both sides of this window",
    "dependency_versions": "dependency versions not recorded on both sides of this window",
}
UNOBSERVED_FACTORS = ("host load and time of day", "which conversations or tasks happened",
                      "model behaviour changes outside Mia's control",
                      "models seen in a window are those that served requests, not configured defaults")
CAUSAL_REASON = ("sequential observations without a controlled comparison cannot establish "
                 "that any listed change caused the metric to move")


def _models_seen(activity: dict[str, Any] | None) -> set[tuple[str, str]] | None:
    usage = (activity or {}).get("inference_usage") or {}
    models = usage.get("models") if usage.get("available") else None
    if not isinstance(models, list):
        return None
    return {(m["provider_id"], m["model_id"]) for m in models}


def _packages(dependencies: dict[str, Any] | None) -> dict[str, str | None] | None:
    if isinstance(dependencies, dict) and dependencies.get("available"):
        return dict(dependencies.get("packages") or {})
    return None


def _evidence_points(store: ObservatoryStore, experiment_id: str) -> list[dict[str, Any]]:
    baseline = store.get_baseline(experiment_id)
    if baseline is None:
        raise ValueError("experiment has no Day 0 baseline")
    manifest = baseline["manifest"]
    points = [{"day_index": 0, "commit": manifest.get("source_commit"), "activity": None,
               "packages": _packages(manifest.get("dependencies")), "models": None}]
    for row in store.list_snapshots(experiment_id):
        if row["day_index"] == 0:
            continue
        snapshot = json.loads(row["snapshot_json"])
        points.append({"day_index": row["day_index"], "commit": row["source_commit"],
                       "activity": snapshot.get("activity"),
                       "packages": _packages(snapshot.get("dependencies")),
                       "models": _models_seen(snapshot.get("activity"))})
    return sorted(points, key=lambda p: p["day_index"])


def _window_changes(before: dict[str, Any], after: dict[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[str]]:
    changes: list[dict[str, Any]] = []
    unknown: list[str] = []
    if before["commit"] != after["commit"]:
        changes.append({"type": "commit", "from": before["commit"], "to": after["commit"]})
    activity = after.get("activity") or {}
    capabilities = activity.get("capabilities") or {}
    if capabilities.get("available") and capabilities.get("transitions"):
        changes.append({"type": "capability_transitions",
                        "counts": dict(sorted(capabilities["transitions"].items())),
                        "owner_approved_actions": capabilities.get("owner_approved_actions")})
    if before["packages"] is None or after["packages"] is None:
        unknown.append("dependency_versions")
    else:
        names = sorted(set(before["packages"]) | set(after["packages"]))
        changed = {n: [before["packages"].get(n), after["packages"].get(n)] for n in names
                   if before["packages"].get(n) != after["packages"].get(n)}
        if changed:
            changes.append({"type": "dependency_versions", "changed": changed})
    if before["models"] is None or after["models"] is None:
        unknown.append("model_or_provider")
    elif before["models"] != after["models"]:
        fmt = lambda pairs: sorted(f"{p}/{m}" for p, m in pairs)  # noqa: E731
        changes.append({"type": "models_used",
                        "added": fmt(after["models"] - before["models"]),
                        "removed": fmt(before["models"] - after["models"])})
    return changes, unknown


def build_attribution(store: ObservatoryStore, experiment_id: str,
                      metric_names: list[str] | None = None) -> dict[str, Any]:
    points = _evidence_points(store, experiment_id)
    windows = []
    for before, after in zip(points, points[1:], strict=False):
        skipped = list(range(before["day_index"] + 1, after["day_index"]))
        changes, unknown = _window_changes(before, after)
        windows.append({"from_day": before["day_index"], "to_day": after["day_index"],
                        "days_without_evidence_inside_window": skipped,
                        "changes": changes, "unknown_factors": unknown})

    samples: dict[str, dict[int, tuple[float, str]]] = {}
    for sample in store.list_metrics():
        if sample["experiment_id"] != experiment_id:
            continue
        if metric_names and sample["metric_name"] not in metric_names:
            continue
        samples.setdefault(sample["metric_name"], {})[sample["day_index"]] = (
            sample["value"], sample["sample_id"])

    metrics: dict[str, list[dict[str, Any]]] = {}
    for name, per_day in sorted(samples.items()):
        days = sorted(per_day)
        rows = []
        for earlier, later in zip(days, days[1:], strict=False):
            span = [w for w in windows if earlier <= w["from_day"] and w["to_day"] <= later]
            changes = [{**c, "window": [w["from_day"], w["to_day"]]}
                       for w in span for c in w["changes"]]
            covered = bool(span) and span[0]["from_day"] == earlier and span[-1]["to_day"] == later
            kinds = {c["type"] for c in changes}
            unknown = sorted({u for w in span for u in w["unknown_factors"]})
            if not covered:
                status = "window_not_covered_by_evidence"
            elif not changes:
                status = "no_recorded_change_in_window"
            elif len(changes) == 1:
                status = "single_candidate_correlation_only"
            else:
                status = "confounded_multiple_candidates"
            rows.append({"from_day": earlier, "to_day": later,
                         "from_value": per_day[earlier][0], "to_value": per_day[later][0],
                         "from_sample_id": per_day[earlier][1], "to_sample_id": per_day[later][1],
                         "delta": per_day[later][0] - per_day[earlier][0],
                         "days_without_evidence_inside_window": sorted(
                             d for w in span for d in w["days_without_evidence_inside_window"]),
                         "unknown_factors_in_window": unknown,
                         "candidate_changes": changes, "candidate_types": sorted(kinds),
                         "status": status, "causal_claim_allowed": False,
                         "causal_reason": CAUSAL_REASON})
        metrics[name] = rows
    return {"experiment_id": experiment_id, "windows": windows, "metrics": metrics,
            "not_attributable": {
                factor: f"{UNKNOWN_REASON[factor]} (windows: "
                        f"{sorted(w['to_day'] for w in windows if factor in w['unknown_factors'])})"
                for factor in UNKNOWN_REASON
                if any(factor in w["unknown_factors"] for w in windows)},
            "unobserved_factors": list(UNOBSERVED_FACTORS),
            "note": ("Candidates are changes recorded in the same window as a metric change. "
                     "They are not causes; no improvement or regression is attributed to them.")}
