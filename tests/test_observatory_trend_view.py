"""MIA-OBS-06: the experiment trends display model and its API, on real stored evidence.

No fakes: a real ObservatoryStore with real snapshots, metrics and events. The properties that
matter are the honest ones: gaps stay gaps, unexplained days are called missed, future days are
not called missed, comparisons exist only when both values were recorded, and evidence is
linked by hash.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.trend_view import DAY_STATES, build_trend_view
from tests.test_experiment_scheduler import passing_manifest
from tests.test_observatory_report_generator import _day, _experiment


def started(store, eid):
    return datetime.fromisoformat(next(e for e in store.list_experiments()
                                       if e["experiment_id"] == eid)["started_at"])


def view_at(store, eid, days_elapsed):
    return build_trend_view(store, eid, now=started(store, eid) + timedelta(days=days_elapsed, hours=1))


def states(view):
    return {d["day"]: d["state"] for d in view["days"]}


def event(store, eid, event_type, day, outcome="failed"):
    return store.record_event(category="experiment", event_type=event_type, summary=event_type,
                              outcome=outcome, day_index=day, experiment_id=eid)


def test_unknown_experiment_has_no_view(tmp_path):
    store, _ = _experiment(tmp_path)
    assert build_trend_view(store, "nope") is None


def test_days_are_captured_due_today_or_not_yet_never_missed_while_still_in_the_future(tmp_path):
    store, eid = _experiment(tmp_path, days=5)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, cpu_percent=11.0)
    view = view_at(store, eid, 3)  # on day 3
    assert states(view) == {0: "captured", 1: "captured", 2: "captured", 3: "due_today",
                            4: "not_yet", 5: "not_yet"}
    assert view["experiment"]["today_index"] == 3
    assert sum(view["day_counts"].values()) == 6 and set(view["day_counts"]) == set(DAY_STATES)


def test_an_unexplained_past_day_is_missed_and_an_explained_one_says_why(tmp_path):
    store, eid = _experiment(tmp_path, days=7)
    _day(store, eid, 1, cpu_percent=10.0)
    event(store, eid, "daily_snapshot_skipped_paused", 2, "skipped")
    event(store, eid, "daily_snapshot_auto_capture_failed", 3)
    event(store, eid, "daily_snapshot_deferred_resource_pressure", 4, "deferred")
    _day(store, eid, 6, cpu_percent=12.0)
    view = view_at(store, eid, 6)
    got = states(view)
    assert got[1] == "captured" and got[2] == "paused" and got[3] == "capture_failed"
    assert got[4] == "deferred" and got[5] == "missed" and got[6] == "captured"
    assert got[7] == "not_yet"
    assert "paused" in view["days"][2]["reasons"][0] and view["days"][2]["event_ids"]
    assert view["days"][5]["reasons"] == ["No snapshot was captured and nothing explains why."]
    assert {p["event_type"] for p in view["problems"]} == {
        "daily_snapshot_skipped_paused", "daily_snapshot_auto_capture_failed",
        "daily_snapshot_deferred_resource_pressure"}


def test_days_the_evidence_marks_unavailable_are_explained_not_missed(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 3, cpu_percent=9.0, missing=[1, 2])
    got = states(view_at(store, eid, 3))
    assert got[1] == got[2] == "unavailable" and got[3] == "captured"


def test_an_incomplete_run_has_no_future_days_left(tmp_path):
    store, eid = _experiment(tmp_path, days=3)
    _day(store, eid, 1, cpu_percent=10.0)
    late = started(store, eid) + timedelta(days=9)
    store.get_snapshot_status(eid, now=late)  # the real clock does this when the page is opened
    view = build_trend_view(store, eid, now=late)
    assert view["experiment"]["status"] == "incomplete"
    assert states(view) == {0: "captured", 1: "captured", 2: "missed", 3: "missed"}
    assert view["day_counts"]["not_yet"] == 0


def test_every_point_links_to_the_snapshot_it_belongs_to(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 1, cpu_percent=10.0)
    store.record_metric(day_index=3, metric_name="cpu_percent", value=14.0, unit="percent",
                        experiment_id=eid)  # a sample on a day with no snapshot
    view = view_at(store, eid, 4)
    manifest = {d["day"]: d["snapshot"]["manifest_sha256"] for d in view["days"] if d["snapshot"]}
    points = {p["day"]: p for p in view["series"][0]["points"]}
    assert points[1]["snapshot_manifest_sha256"] == manifest[1]
    assert points[3]["snapshot_manifest_sha256"] is None  # honest: no evidence file for that day
    assert view["evidence"]["snapshot_manifest_sha256"] == {str(k): v for k, v in manifest.items()}
    assert len(view["evidence"]["report_sha256"]) == 64


def test_a_gap_stays_a_gap_and_nothing_is_interpolated(tmp_path):
    store, eid = _experiment(tmp_path, days=6)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 5, cpu_percent=20.0)
    series = view_at(store, eid, 5)["series"][0]
    assert [(p["day"], p["value"]) for p in series["points"]] == [(1, 10.0), (5, 20.0)]
    assert series["windows"][0]["days_without_evidence_inside_window"] == [2, 3, 4]


def test_before_after_exists_only_when_two_days_and_one_unit_were_recorded(tmp_path):
    store, eid = _experiment(tmp_path, days=6)
    _day(store, eid, 1, cpu_percent=10.0)
    one = view_at(store, eid, 2)["series"][0]["before_after"]
    assert one["comparable"] is False and "Only one day" in one["reason"]
    _day(store, eid, 3, cpu_percent=16.0)
    two = view_at(store, eid, 4)["series"][0]["before_after"]
    assert two["comparable"] is True and two["delta"] == 6.0 and two["unit"] == "percent"
    assert (two["from"]["day"], two["to"]["day"], two["starts_at_day_zero"]) == (1, 3, False)
    assert "not a claim" in two["note"]
    # Registered metrics refuse a wrong unit (OBS-01), so mixed units can only come from a custom one.
    for day, unit in ((1, "percent"), (2, "ratio")):
        store.record_metric(day_index=day, metric_name="custom_probe", value=0.5, unit=unit,
                            experiment_id=eid)
    mixed = next(s for s in view_at(store, eid, 4)["series"] if s["name"] == "custom_probe")
    assert mixed["mixed_units"] is True and mixed["unit"] is None
    assert mixed["before_after"]["comparable"] is False and "units" in mixed["before_after"]["reason"]


def test_a_zero_value_is_kept_as_zero_and_a_missing_one_is_absent(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 1, errors=0.0)
    series = view_at(store, eid, 2)["series"][0]
    assert series["points"][0]["value"] == 0.0 and len(series["points"]) == 1


def test_small_samples_are_flagged_and_the_threshold_is_shown(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 1, cpu_percent=10.0)
    size = view_at(store, eid, 2)["series"][0]["sample_size"]
    assert size["samples"] == 1 and size["small_sample"] is True and size["threshold"] >= 2


def test_changes_are_candidates_never_causes(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 1, cpu_percent=10.0)
    _day(store, eid, 2, cpu_percent=30.0, commit="b" * 40)
    view = view_at(store, eid, 3)
    assert all(w["causal_claim_allowed"] is False for s in view["series"] for w in s["windows"])
    assert any(c["type"] == "commit" for m in view["change_markers"] for c in m["changes"])
    assert view["claims_not_allowed"] and view["limitations"] and view["confounders"]


def test_building_the_view_writes_nothing_and_is_repeatable(tmp_path):
    store, eid = _experiment(tmp_path, days=4)
    _day(store, eid, 1, cpu_percent=10.0)
    before = (len(store.list_events(limit=1000)), len(store.list_metrics()))
    first, second = view_at(store, eid, 2), view_at(store, eid, 2)
    assert first == second
    assert (len(store.list_events(limit=1000)), len(store.list_metrics())) == before


def test_a_new_experiment_with_no_daily_evidence_is_marked_empty(tmp_path):
    store, eid = _experiment(tmp_path, days=3)
    assert view_at(store, eid, 0)["empty"] is True
    _day(store, eid, 1, cpu_percent=10.0)
    assert view_at(store, eid, 1)["empty"] is False


def test_tampered_evidence_is_refused_not_shown(tmp_path):
    store, eid = _experiment(tmp_path, days=3)
    _day(store, eid, 1, cpu_percent=10.0)
    db = sqlite3.connect(store.database_path)
    try:
        db.execute("DROP TRIGGER IF EXISTS daily_snapshots_no_update")
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")
        db.execute("UPDATE daily_snapshots SET snapshot_json = replace(snapshot_json, 'day_index', 'x')"
                   " WHERE day_index = 1")
        db.commit()
    finally:
        db.close()
    with pytest.raises(ValueError, match="hash does not match"):
        build_trend_view(store, eid)


# ------------------------------------------------------------------------------------ API


def test_api_returns_the_view_404s_unknown_and_reports_integrity_failures(tmp_path):
    store, eid = _experiment(tmp_path, days=3)
    _day(store, eid, 1, cpu_percent=10.0)
    client = TestClient(create_app(store=store))
    body = client.get(f"/api/experiments/{eid}/trends").json()
    assert body["experiment"]["experiment_id"] == eid and body["series"][0]["name"] == "cpu_percent"
    assert client.get("/api/experiments/missing/trends").status_code == 404
    db = sqlite3.connect(store.database_path)
    try:
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")
        db.execute("UPDATE daily_snapshots SET snapshot_json = snapshot_json || ' ' WHERE day_index = 1")
        db.commit()
    finally:
        db.close()
    failed = client.get(f"/api/experiments/{eid}/trends")
    assert failed.status_code == 500 and "hash does not match" in failed.json()["detail"]


def test_baseline_manifest_helper_is_the_real_one():
    assert passing_manifest()["source_commit"]
