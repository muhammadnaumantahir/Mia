"""Display model for the experiment trends page (MIA-OBS-06).

Everything here is read from stored evidence; nothing is estimated, smoothed, interpolated or
invented. A day that has no snapshot is shown as what it is (missed, paused, deferred, failed,
not reached yet), a metric gap stays a gap, and a before/after pair exists only where both
values were really recorded in the same unit. Each point carries the hash of the snapshot it
belongs to, so a reader can follow the number back to its evidence.

It composes the integrity-checked ``build_experiment_report``, the OBS-07 ``generate_report`` and
the OBS-04 attribution windows. It writes nothing (reading a report may close a run whose final
capture window has already passed; that is existing behaviour of the report itself).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .attribution import build_attribution
from .benchmarks import SMALL_SAMPLE
from .report_generator import generate_report
from .store import ObservatoryStore

# Explicit reasons a day has no snapshot, by the event that explains it (first match wins).
_REASONS = (
    ("daily_snapshot_skipped_paused", "paused", "The owner had the scheduler paused."),
    ("daily_snapshot_auto_capture_failed", "capture_failed", "The automatic capture failed."),
    ("daily_snapshot_deferred_resource_pressure", "deferred",
     "The capture was deferred because the host was under resource pressure."),
    ("daily_snapshot_deferred_telemetry_unavailable", "deferred",
     "The capture was deferred because telemetry was unavailable."),
)
_PROBLEM_EVENTS = {event for event, *_ in _REASONS} | {
    "daily_snapshots_unavailable", "daily_snapshot_scheduler_error"}

DAY_STATES = ("captured", "missed", "paused", "capture_failed", "deferred", "unavailable",
              "due_today", "not_yet")


def _today_index(experiment: dict[str, Any], now: datetime) -> int | None:
    """Day number the experiment is on, or None when it is not running."""
    if experiment["status"] != "running" or not experiment.get("started_at"):
        return None
    started = datetime.fromisoformat(experiment["started_at"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0, int((now - started).total_seconds() // 86400))


def _days(base: dict[str, Any], today: int | None) -> list[dict[str, Any]]:
    planned = base["experiment"]["planned_days"]
    status = base["experiment"]["status"]
    by_day = {s["day_index"]: s for s in base["snapshots"]}
    events: dict[int, list[dict[str, Any]]] = {}
    for event in base["events"]:
        day = event.get("day_index")
        if isinstance(day, int) and not isinstance(day, bool):
            events.setdefault(day, []).append(event)
    explained = set(base["days"]["unavailable"])
    out = []
    for day in range(0, planned + 1):
        row: dict[str, Any] = {"day": day, "state": None, "reasons": [], "snapshot": None,
                               "event_ids": []}
        snap = by_day.get(day)
        if snap is not None:
            row.update(state="captured", snapshot={
                "manifest_sha256": snap["manifest_sha256"], "source_commit": snap["source_commit"],
                "captured_at": snap["captured_at"]})
            out.append(row)
            continue
        found = []
        for event_type, state, text in _REASONS:
            hit = [e for e in events.get(day, []) if e["event_type"] == event_type]
            if hit:
                found.append((state, text, [e["event_id"] for e in hit]))
        if found:
            row["state"] = found[0][0]
            row["reasons"] = [text for _, text, _ in found]
            row["event_ids"] = [i for _, _, ids in found for i in ids]
        elif day in explained:
            row.update(state="unavailable", reasons=[
                "The evidence says this day could not be captured (recorded as unavailable)."])
        elif status in {"completed", "incomplete"} or (today is not None and day < today):
            row.update(state="missed", reasons=["No snapshot was captured and nothing explains why."])
        elif today is not None and day == today:
            row.update(state="due_today", reasons=["This day's capture window is still open."])
        else:
            row.update(state="not_yet", reasons=["This day has not been reached yet."])
        out.append(row)
    return out


def _series(base: dict[str, Any], report: dict[str, Any], snapshots: dict[int, dict]) -> list:
    sizes = report["sample_sizes"]["metrics"]
    changes = report["metric_changes"]
    out = []
    for name, points in sorted(base["metric_series"].items()):
        units = sorted({p["unit"] for p in points})
        shaped = [{"day": p["day_index"], "value": p["value"], "unit": p["unit"],
                   "benchmark_id": p.get("benchmark_id"), "occurred_at": p["occurred_at"],
                   "snapshot_manifest_sha256": (snapshots.get(p["day_index"]) or {}).get(
                       "manifest_sha256")} for p in points]
        distinct_days = sorted({p["day"] for p in shaped})
        before_after: dict[str, Any]
        if len(distinct_days) < 2:
            before_after = {"comparable": False, "reason": "Only one day has a recorded value."}
        elif len(units) > 1:
            before_after = {"comparable": False, "reason": "The recorded values use different units."}
        else:
            first = next(p for p in shaped if p["day"] == distinct_days[0])
            last = [p for p in shaped if p["day"] == distinct_days[-1]][-1]
            before_after = {
                "comparable": True, "unit": units[0],
                "from": {"day": first["day"], "value": first["value"]},
                "to": {"day": last["day"], "value": last["value"]},
                "delta": last["value"] - first["value"],
                "starts_at_day_zero": first["day"] == 0,
                "note": "An observed difference between two recorded values. It is not a "
                        "claim that anything improved or caused it."}
        size = sizes.get(name, {"samples": len(points), "days": distinct_days,
                                "small_sample": len(points) < SMALL_SAMPLE})
        out.append({"name": name, "unit": units[0] if len(units) == 1 else None,
                    "mixed_units": len(units) > 1, "points": shaped,
                    "sample_size": {"samples": size["samples"], "days": size["days"],
                                    "small_sample": size["small_sample"],
                                    "threshold": SMALL_SAMPLE},
                    "before_after": before_after,
                    "windows": [{k: row[k] for k in (
                        "from_day", "to_day", "from_value", "to_value", "delta", "status",
                        "candidate_types", "days_without_evidence_inside_window",
                        "causal_claim_allowed")} for row in changes.get(name, [])]})
    return out


def build_trend_view(store: ObservatoryStore, experiment_id: str, *,
                     now: datetime | None = None) -> dict[str, Any] | None:
    """The display model for one experiment, or None when it does not exist.

    Raises ``ValueError`` when the stored evidence fails its integrity checks; the caller must
    show that failure rather than a partial picture.
    """
    base = store.build_experiment_report(experiment_id)
    if base is None:
        return None
    report = generate_report(store, experiment_id)
    if report is None:  # pragma: no cover - the experiment was just read
        return None
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    today = _today_index(base["experiment"], instant)
    snapshots = {s["day_index"]: s for s in base["snapshots"]}
    days = _days(base, today)
    windows = build_attribution(store, experiment_id)["windows"]
    problems = [{"event_id": e["event_id"], "day": e.get("day_index"),
                 "event_type": e["event_type"], "outcome": e.get("outcome"),
                 "summary": e["summary"], "occurred_at": e["occurred_at"]}
                for e in base["events"]
                if e["event_type"] in _PROBLEM_EVENTS or e.get("outcome") == "failed"]
    counts = {state: sum(1 for d in days if d["state"] == state) for state in DAY_STATES}
    return {
        "experiment": {**base["experiment"], "today_index": today},
        "day_counts": counts,
        "days": days,
        "series": _series(base, report, snapshots),
        "change_markers": [{"from_day": w["from_day"], "to_day": w["to_day"],
                            "changes": [{"type": c["type"]} for c in w["changes"]],
                            "unknown_factors": w["unknown_factors"],
                            "days_without_evidence_inside_window":
                                w["days_without_evidence_inside_window"]} for w in windows],
        "problems": problems,
        "benchmarks": report["benchmarks"],
        "timing": report["timing"],
        "rollbacks": report["rollbacks"],
        "confounders": report["confounders"],
        "limitations": report["limitations"],
        "claims_not_allowed": report["claims_not_allowed"],
        "evidence": {"report_sha256": report["report_sha256"],
                     "baseline_sha256": report["overview"]["baseline_sha256"],
                     "snapshot_manifest_sha256": report["evidence"]["snapshot_manifest_sha256"],
                     "event_count": report["evidence"]["event_count"],
                     "metric_count": report["evidence"]["metric_count"]},
        "empty": not base["snapshots"] or (len(base["snapshots"]) == 1 and not base["metric_series"]),
    }
