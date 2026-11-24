"""MIA-OBS-08: owner stop, verifiable export and a truthful privacy notice.

Real store, real hashes, real baseline/snapshot capture code (with the existing fake subprocess
runner). The privacy notice is checked against what the real capture actually records.
"""

import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from mia.observatory import experiment_export as ex
from mia.observatory.api import create_app
from mia.observatory.baseline import NOT_CAPTURED, capture_baseline_manifest
from mia.observatory.snapshot_capture import ExperimentSnapshotCapturer
from mia.observatory.snapshot_scheduler import SKIPPED_PAUSED_EVENT, ExperimentSnapshotScheduler
from mia.observatory.store import ObservatoryStore

from .test_baseline_capture import FakeSubprocess, _write_baseline_files
from .test_experiment_scheduler import FakeResources, create_due_experiment, passing_manifest


def make_client(tmp_path, monkeypatch, store, **kwargs):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    return TestClient(create_app(
        store=store, baseline_capture=lambda root: passing_manifest(),
        resource_monitor=FakeResources(), **kwargs))


def capture_day_one(store):
    capturer = ExperimentSnapshotCapturer(
        observatory=store, baseline_capture=lambda root: passing_manifest(),
        resource_monitor=FakeResources(), repo_root=store.database_path.parent)
    return capturer


@pytest.fixture
def running(tmp_path, monkeypatch):
    store, eid = create_due_experiment(tmp_path)  # planned 3 days, started 25 h ago: Day 1 is due
    return store, eid, make_client(tmp_path, monkeypatch, store)


# ---- stop ---------------------------------------------------------------------------------

def test_stop_ends_a_running_experiment_terminally_and_records_it(running):
    store, eid, client = running
    response = client.post(f"/api/experiments/{eid}/stop")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stopped" and body["recorded_days"] == [0] and body["stopped_at"]
    experiment = next(e for e in store.list_experiments() if e["experiment_id"] == eid)
    assert experiment["status"] == "stopped" and experiment["completed_at"] == body["stopped_at"]
    event = [e for e in store.list_events(50) if e["event_type"] == "experiment_stopped"][0]
    assert event["outcome"] == "stopped" and json.loads(event["payload_json"])["stopped_by"] == "owner"
    report = client.get(f"/api/experiments/{eid}/report").json()
    assert report["experiment"]["conclusion"] == "stopped_by_owner" and report["report_ready"] is True
    assert report["experiment"]["status"] == "stopped"


def test_stopped_is_never_reported_as_completed_and_cannot_be_restarted_or_captured(running):
    store, eid, client = running
    client.post(f"/api/experiments/{eid}/stop")
    assert client.post(f"/api/experiments/{eid}/stop").status_code == 409  # not running any more
    assert client.post(f"/api/experiments/{eid}/start").status_code == 409
    with pytest.raises(ValueError, match="must be running"):
        store.record_daily_snapshot(experiment_id=eid, day_index=1, source_commit="a" * 40, snapshot={})
    assert client.post(f"/api/experiments/{eid}/snapshots/capture").status_code in (409, 422)
    assert [s["day_index"] for s in store.list_snapshots(eid)] == [0]
    assert store.build_experiment_report(eid)["experiment"]["conclusion"] != "completed"


def test_stop_refuses_prepared_and_unknown_experiments(tmp_path, monkeypatch):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    eid = store.prepare_experiment(name="P", baseline_manifest=passing_manifest(), planned_days=3)["experiment_id"]
    client = make_client(tmp_path, monkeypatch, store)
    assert client.post(f"/api/experiments/{eid}/stop").status_code == 409
    assert client.post("/api/experiments/does-not-exist/stop").status_code == 409
    assert next(e for e in store.list_experiments() if e["experiment_id"] == eid)["status"] == "prepared"


@pytest.mark.asyncio
async def test_scheduler_ignores_a_stopped_experiment(running):
    store, eid, client = running
    client.post(f"/api/experiments/{eid}/stop")
    resources = FakeResources()
    scheduler = ExperimentSnapshotScheduler(store, capture_day_one(store), resources, enabled=True,
                                            interval_seconds=15)
    assert await scheduler.run_once() == [] and resources.calls == 0


def test_days_whose_window_never_opened_are_not_run_rather_than_unaccounted(running):
    store, eid, client = running  # Day 1 window is open (started 25 h ago); Days 2 and 3 are not
    client.post(f"/api/experiments/{eid}/stop")
    days = client.get(f"/api/experiments/{eid}/report").json()["days"]
    assert days["recorded"] == [0]
    assert days["not_run_after_stop"] == [2, 3]
    assert days["unaccounted"] == [1]  # a day that was due and not captured is still not hidden
    assert days["unavailable"] == []


def test_a_paused_due_day_is_explained_and_a_captured_day_is_recorded_before_stop(running):
    store, eid, client = running
    store.record_event(category="experiment", event_type=SKIPPED_PAUSED_EVENT, summary="paused",
                       experiment_id=eid, day_index=1)
    client.post(f"/api/experiments/{eid}/stop")
    days = store.build_experiment_report(eid)["days"]
    assert days["unavailable"] == [1] and days["unaccounted"] == [] and days["not_run_after_stop"] == [2, 3]


def test_a_captured_day_before_stop_stays_recorded(running):
    store, eid, client = running
    assert capture_day_one(store).capture(eid)["status"] == "recorded"
    client.post(f"/api/experiments/{eid}/stop")
    days = store.build_experiment_report(eid)["days"]
    assert days["recorded"] == [0, 1] and days["unaccounted"] == [] and days["not_run_after_stop"] == [2, 3]


def test_non_stopped_reports_have_an_empty_not_run_list(running):
    store, eid, _ = running
    assert store.build_experiment_report(eid)["days"]["not_run_after_stop"] == []


# ---- export -------------------------------------------------------------------------------

def export_bundle(client, eid):
    response = client.get(f"/api/experiments/{eid}/export")
    assert response.status_code == 200
    return response, json.loads(response.text)


def test_export_is_a_downloadable_verifiable_bundle(running):
    store, eid, client = running
    capture_day_one(store).capture(eid)
    client.post(f"/api/experiments/{eid}/stop")
    response, bundle = export_bundle(client, eid)
    assert response.headers["content-type"].startswith("application/json")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and f"mia-experiment-{eid}.json" in disposition
    assert bundle["format"] == ex.EXPORT_FORMAT and bundle["final"] is True
    assert bundle["privacy_notice"]["version"] == ex.PRIVACY_NOTICE_VERSION
    assert bundle["report"]["experiment"]["conclusion"] == "stopped_by_owner"
    assert [s["day_index"] for s in bundle["report"]["snapshots"]] == [0, 1]
    assert ex.verify_export(bundle) == []
    assert bundle["integrity"]["content_sha256"] == export_bundle(client, eid)[1]["integrity"]["content_sha256"]


def test_export_of_a_running_experiment_is_marked_not_final(running):
    store, eid, client = running
    _, bundle = export_bundle(client, eid)
    assert bundle["final"] is False and ex.verify_export(bundle) == []


def test_export_of_unknown_experiment_is_404(running):
    assert running[2].get("/api/experiments/nope/export").status_code == 404


def test_export_fails_closed_when_stored_evidence_no_longer_verifies(running):
    store, eid, client = running
    capture_day_one(store).capture(eid)
    with sqlite3.connect(store.database_path) as db:
        # The database refuses edits through its own triggers, so simulate someone with direct
        # file access who removes them first; the hash check is the second line of defence.
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE daily_snapshots SET snapshot_json = 'x' WHERE experiment_id = ?", (eid,))
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall():
            db.execute(f'DROP TRIGGER "{name}"')
        db.execute("UPDATE daily_snapshots SET snapshot_json = REPLACE(snapshot_json, 'passed', 'faked') "
                   "WHERE experiment_id = ? AND day_index = 1", (eid,))
    response = client.get(f"/api/experiments/{eid}/export")
    assert response.status_code == 409 and "failed verification" in response.json()["detail"]
    assert "attachment" not in response.headers.get("content-disposition", "")


def mutated(bundle, path, value):
    clone = copy.deepcopy(bundle)
    target = clone
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return clone


def test_verifier_detects_every_kind_of_tampering(running):
    store, eid, client = running
    capture_day_one(store).capture(eid)
    client.post(f"/api/experiments/{eid}/stop")
    _, bundle = export_bundle(client, eid)
    assert ex.verify_export(bundle) == []
    day1 = bundle["report"]["snapshots"][1]
    tampered = {
        "snapshot evidence": mutated(bundle, ["report", "snapshots", 1, "evidence", "verification_passed"], False),
        "snapshot hash": mutated(bundle, ["report", "snapshots", 1, "manifest_sha256"], "0" * 64),
        "baseline manifest": mutated(bundle, ["report", "baseline", "manifest", "source_commit"], "f" * 40),
        "baseline hash": mutated(bundle, ["report", "baseline", "sha256"], "0" * 64),
        "conclusion": mutated(bundle, ["report", "experiment", "conclusion"], "completed"),
        "privacy notice": mutated(bundle, ["privacy_notice", "never_collected"], []),
        "final flag": mutated(bundle, ["final"], not bundle["final"]),
        "content hash": mutated(bundle, ["integrity", "content_sha256"], "0" * 64),
    }
    for name, candidate in tampered.items():
        assert ex.verify_export(candidate), f"{name} tampering was not detected"
    assert any(f"Day {day1['day_index']}" in p for p in ex.verify_export(tampered["snapshot hash"]))
    assert ex.verify_export(mutated(bundle, ["exported_at"], "1999-01-01T00:00:00+00:00")) == []  # not hashed


def rehashed(bundle):
    """An attacker who also recomputes the outer hash (it is a checksum, not a signature)."""
    clone = copy.deepcopy(bundle)
    content = ex._content(clone["report"], clone["privacy_notice"], bool(clone["final"]))
    clone["integrity"]["content_sha256"] = ex.sha256_text(ex.canonical(content))
    return clone


def test_inner_hashes_catch_edits_even_when_the_outer_hash_is_recomputed(running):
    store, eid, client = running
    capture_day_one(store).capture(eid)
    client.post(f"/api/experiments/{eid}/stop")
    _, bundle = export_bundle(client, eid)
    assert ex.verify_export(rehashed(bundle)) == []  # control: rehashing an intact bundle is fine
    cases = {
        "snapshot evidence edited": mutated(bundle, ["report", "snapshots", 1, "evidence", "verification_passed"], False),
        "snapshot hash edited": mutated(bundle, ["report", "snapshots", 1, "manifest_sha256"], "0" * 64),
        "baseline manifest edited": mutated(bundle, ["report", "baseline", "manifest", "source_commit"], "f" * 40),
        "baseline hash edited": mutated(bundle, ["report", "baseline", "sha256"], "0" * 64),
        "day 1 pointed at another baseline": mutated(bundle, ["report", "snapshots", 1, "evidence", "baseline_sha256"], "0" * 64),
        "day 0 replaced": mutated(bundle, ["report", "snapshots", 0, "evidence", "source_commit"], "e" * 40),
    }
    for name, candidate in cases.items():
        assert ex.verify_export(rehashed(candidate)), f"{name} went undetected after rehashing"


def reseal_snapshot(bundle, index, **changes):
    """Edit one snapshot's evidence and fix its own hash so that only cross-checks can notice."""
    clone = copy.deepcopy(bundle)
    snapshot = clone["report"]["snapshots"][index]
    snapshot["evidence"].update(changes)
    snapshot["manifest_sha256"] = ex.sha256_text(ex.canonical(snapshot["evidence"]))
    return rehashed(clone)


def test_cross_checks_catch_a_self_consistent_snapshot_from_a_different_baseline(running):
    store, eid, client = running
    capture_day_one(store).capture(eid)
    client.post(f"/api/experiments/{eid}/stop")
    _, bundle = export_bundle(client, eid)
    other_baseline = reseal_snapshot(bundle, 1, baseline_sha256="0" * 64)
    problems = ex.verify_export(other_baseline)
    assert problems == ["Day 1 snapshot belongs to a different baseline"]
    swapped_day0 = reseal_snapshot(bundle, 0, source_commit="e" * 40)
    assert ex.verify_export(swapped_day0) == ["the Day 0 snapshot is not the recorded baseline"]


def test_a_fully_consistent_rewrite_of_an_unhashed_field_is_not_detectable_and_we_say_so(running):
    """Documented limit: content_sha256 is a checksum, not a signature. Anchor it elsewhere."""
    store, eid, client = running
    client.post(f"/api/experiments/{eid}/stop")
    _, bundle = export_bundle(client, eid)
    renamed = rehashed(mutated(bundle, ["report", "experiment", "name"], "A different name"))
    assert ex.verify_export(renamed) == []
    assert "not a signature" in ex.__doc__ and "content_sha256" in ex.__doc__


@pytest.mark.parametrize("bad", [None, [], "text", {}, {"format": "other"},
                                 {"format": ex.EXPORT_FORMAT, "version": 99},
                                 {"format": ex.EXPORT_FORMAT, "version": 1}])
def test_verifier_rejects_malformed_input_without_crashing(bad):
    assert ex.verify_export(bad)


def test_offline_cli_verifies_and_flags_changes(running, tmp_path, capsys):
    store, eid, client = running
    client.post(f"/api/experiments/{eid}/stop")
    _, bundle = export_bundle(client, eid)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(bundle), encoding="utf-8")
    assert ex.main(["verify", str(good)]) == 0 and "verified" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(mutated(bundle, ["report", "experiment", "name"], "Renamed")), encoding="utf-8")
    assert ex.main(["verify", str(bad)]) == 1 and "PROBLEM" in capsys.readouterr().out
    assert ex.main(["verify", str(tmp_path / "missing.json")]) == 1
    (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")
    assert ex.main(["verify", str(tmp_path / "junk.json")]) == 1
    assert ex.main([]) == 2


# ---- privacy notice and start -------------------------------------------------------------

def test_privacy_notice_states_storage_exclusions_controls_and_live_caps(running):
    store, eid, client = running
    notice = client.get("/api/experiments/privacy-notice").json()
    assert notice["version"] == ex.PRIVACY_NOTICE_VERSION
    assert "Only on this computer" in notice["stored"] and "no network" in notice["stored"]
    assert any("conversation" in item for item in notice["never_collected"])
    assert any("credentials" in item or "keys" in item for item in notice["never_collected"])
    assert set(notice["controls"]) == {"start", "pause_resume", "stop", "export"}
    status = client.get("/api/experiments/scheduler/status").json()
    caps = notice["resource_caps"]
    assert (caps["max_cpu_percent"], caps["max_memory_percent"], caps["check_interval_seconds"],
            caps["retry_seconds"]) == (status["max_cpu_percent"], status["max_memory_percent"],
                                       status["interval_seconds"], status["retry_seconds"])
    assert notice["not_captured_day0"] == NOT_CAPTURED
    assert notice["automatic_capture_enabled"] == status["enabled"]


def test_privacy_notice_discloses_the_host_name_and_environment_details(running):
    notice = running[2].get("/api/experiments/privacy-notice").json()
    fields = {f["field"]: f["what"] for g in notice["collected"] for f in g["fields"]}
    assert "host name" in fields["baseline_results"] and "capture_host" in fields["baseline_results"]
    assert "CPU model" in fields["environment"] and "Operating system" in fields["environment"]


def test_notice_covers_exactly_what_the_real_capture_records(tmp_path, monkeypatch):
    """Runs the real baseline and snapshot code; a new recorded field must be disclosed first."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    root = tmp_path / "repo"
    root.mkdir()
    _write_baseline_files(root)
    manifest = capture_baseline_manifest(root, subprocess_run=FakeSubprocess())
    assert manifest["baseline_results"]["capture_host"]  # the host name really is recorded
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    eid = store.prepare_experiment(name="Real shape", baseline_manifest=manifest,
                                   planned_days=3)["experiment_id"]
    store.start_experiment(eid)
    with sqlite3.connect(store.database_path) as db:
        db.execute("UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
                   ((datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat(), eid))
    capturer = ExperimentSnapshotCapturer(observatory=store, baseline_capture=lambda r: manifest,
                                          resource_monitor=FakeResources(), repo_root=root)
    capturer.capture(eid)
    snapshot = next(s for s in store.build_experiment_report(eid)["snapshots"] if s["day_index"] == 1)["evidence"]
    recorded = set(manifest) | set(snapshot)
    disclosed = set(ex.FIELD_DISCLOSURE)
    assert recorded <= disclosed, f"recorded but not in the privacy notice: {sorted(recorded - disclosed)}"
    assert disclosed <= recorded, f"claimed in the notice but never recorded: {sorted(disclosed - recorded)}"
    assert set(manifest["environment"]) == {
        "os", "os_release", "platform", "python", "python_implementation", "machine", "processor",
        "cpu_count", "memory_total_bytes"}  # a new environment field needs a notice update


def test_start_records_notice_acknowledgement_and_caps(tmp_path, monkeypatch):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    client = make_client(tmp_path, monkeypatch, store)
    ids = [store.prepare_experiment(name=n, baseline_manifest=passing_manifest(), planned_days=3)["experiment_id"]
           for n in ("one",)]
    started = client.post(f"/api/experiments/{ids[0]}/start", json={"privacy_notice_version": 1})
    assert started.status_code == 200
    payload = json.loads(next(e for e in store.list_events(20) if e["event_type"] == "experiment_started")["payload_json"])
    assert payload["privacy_notice_version_acknowledged"] == 1
    assert payload["resource_caps"]["max_cpu_percent"] and payload["started_by"] == "local-dashboard"


def test_start_without_acknowledgement_still_works_and_says_so(tmp_path, monkeypatch):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    client = make_client(tmp_path, monkeypatch, store)
    eid = store.prepare_experiment(name="n", baseline_manifest=passing_manifest(), planned_days=3)["experiment_id"]
    assert client.post(f"/api/experiments/{eid}/start", json={"privacy_notice_version": "x"}).status_code == 422
    assert next(e for e in store.list_experiments() if e["experiment_id"] == eid)["status"] == "prepared"
    assert client.post(f"/api/experiments/{eid}/start").status_code == 200  # existing clients keep working
    payload = json.loads(next(e for e in store.list_events(20) if e["event_type"] == "experiment_started")["payload_json"])
    assert payload["privacy_notice_version_acknowledged"] is None
