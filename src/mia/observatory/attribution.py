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

NOT_ATTRIBUTABLE = {
    "model_or_provider": "per-snapshot usage totals carry no model or provider breakdown",
    "dependency_versions": "daily snapshots do not store dependency versions",
}
UNOBSERVED_FACTORS = ("host load and time of day", "which conversations or tasks happened",
                      "model behaviour changes outside Mia's control")
CAUSAL_REASON = ("sequential observations without a controlled comparison cannot establish "
                 "that any listed change caused the metric to move")


def _evidence_points(store: ObservatoryStore, experiment_id: str) -> list[dict[str, Any]]:
    baseline = store.get_baseline(experiment_id)
    if baseline is None:
        raise ValueError("experiment has no Day 0 baseline")
    points = [{"day_index": 0, "commit": baseline["manifest"].get("source_commit"),
               "activity": None}]
    for row in store.list_snapshots(experiment_id):
        if row["day_index"] == 0:
            continue
        snapshot = json.loads(row["snapshot_json"])
        points.append({"day_index": row["day_index"], "commit": row["source_commit"],
                       "activity": snapshot.get("activity")})
    return sorted(points, key=lambda p: p["day_index"])


def _window_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if before["commit"] != after["commit"]:
        changes.append({"type": "commit", "from": before["commit"], "to": after["commit"]})
    activity = after.get("activity") or {}
    capabilities = activity.get("capabilities") or {}
    if capabilities.get("available") and capabilities.get("transitions"):
        changes.append({"type": "capability_transitions",
                        "counts": dict(sorted(capabilities["transitions"].items())),
                        "owner_approved_actions": capabilities.get("owner_approved_actions")})
    return changes


def build_attribution(store: ObservatoryStore, experiment_id: str,
                      metric_names: list[str] | None = None) -> dict[str, Any]:
    points = _evidence_points(store, experiment_id)
    windows = []
    for before, after in zip(points, points[1:], strict=False):
        skipped = list(range(before["day_index"] + 1, after["day_index"]))
        windows.append({"from_day": before["day_index"], "to_day": after["day_index"],
                        "days_without_evidence_inside_window": skipped,
                        "changes": _window_changes(before, after)})

    samples: dict[str, dict[int, float]] = {}
    for sample in store.list_metrics():
        if sample["experiment_id"] != experiment_id:
            continue
        if metric_names and sample["metric_name"] not in metric_names:
            continue
        samples.setdefault(sample["metric_name"], {})[sample["day_index"]] = sample["value"]

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
            if not covered:
                status = "window_not_covered_by_evidence"
            elif not changes:
                status = "no_recorded_change_in_window"
            elif len(changes) == 1:
                status = "single_candidate_correlation_only"
            else:
                status = "confounded_multiple_candidates"
            rows.append({"from_day": earlier, "to_day": later,
                         "from_value": per_day[earlier], "to_value": per_day[later],
                         "delta": per_day[later] - per_day[earlier],
                         "days_without_evidence_inside_window": sorted(
                             d for w in span for d in w["days_without_evidence_inside_window"]),
                         "candidate_changes": changes, "candidate_types": sorted(kinds),
                         "status": status, "causal_claim_allowed": False,
                         "causal_reason": CAUSAL_REASON})
        metrics[name] = rows
    return {"experiment_id": experiment_id, "windows": windows, "metrics": metrics,
            "not_attributable": dict(NOT_ATTRIBUTABLE),
            "unobserved_factors": list(UNOBSERVED_FACTORS),
            "note": ("Candidates are changes recorded in the same window as a metric change. "
                     "They are not causes; no improvement or regression is attributed to them.")}
