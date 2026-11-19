import json
import sqlite3
from datetime import UTC, datetime, timedelta

from test_experiment_scheduler import FakeResources, create_due_experiment, passing_manifest

from mia.agent.tasks import TaskStore
from mia.evolution.capabilities import CapabilityStore
from mia.gateway.ledger import UsageLedger
from mia.observatory.activity import ExperimentActivityCollector
from mia.observatory.snapshot_capture import ExperimentSnapshotCapturer


def stores(tmp_path):
    ledger = UsageLedger(tmp_path / "u.sqlite3")
    caps = CapabilityStore(tmp_path / "c.sqlite3", tmp_path / "gen", activation_enabled=True)
    tasks = TaskStore(tmp_path / "t.sqlite3")
    return ledger, caps, tasks


def record_usage(ledger, cost, free=False):
    ledger.record(provider_id="p", model_id="m", task_type="chat", input_tokens=10,
                  output_tokens=5, estimated_input_tokens=1, estimated_output_tokens=1,
                  cost_usd=cost, free_tier=free, data_local=free)


def test_collector_reports_real_counts_and_marks_broken_sources_unavailable(tmp_path):
    ledger, caps, tasks = stores(tmp_path)
    record_usage(ledger, 0.5)
    record_usage(ledger, 0.0, free=True)
    task_id = tasks.create_task(title="t", goal="goal text", source="interactive")
    tasks.transition(task_id, "approved", reason="ok", actor="local-user")
    since = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    until = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    class Broken:
        def event_counts(self, since, until):
            raise RuntimeError("db locked")

    activity = ExperimentActivityCollector(
        usage_ledger=ledger, capability_store=Broken(), task_store=tasks
    ).collect(since, until)
    usage = activity["inference_usage"]
    assert usage["available"] and usage["requests"] == 2 and usage["cost_usd"] == 0.5
    assert usage["paid_requests"] == 1 and usage["non_local_requests"] == 1
    assert activity["capabilities"] == {"available": False, "reason": "RuntimeError"}
    assert activity["tasks"]["transitions"] == {"proposed": 1, "approved": 1} or \
        activity["tasks"]["transitions"].get("approved") == 1
    assert activity["tasks"]["owner_actions"] == 1
    assert "not linked to experiment IDs" in activity["attribution"]
    empty = ExperimentActivityCollector().collect(since, until)
    assert empty["tasks"]["available"] is False  # unconfigured is not zero


def test_window_excludes_events_outside_it(tmp_path):
    ledger, _, _ = stores(tmp_path)
    record_usage(ledger, 1.0)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    assert ledger.window_summary(future, later)["requests"] == 0


def test_snapshot_stores_activity_and_report_aggregates_it(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    ledger, caps, tasks = stores(tmp_path)
    started = store.get_snapshot_status(experiment_id)["started_at"]
    record_usage(ledger, 0.25)
    with sqlite3.connect(ledger.database_path) as db:  # place the event inside the window
        db.execute("UPDATE usage SET recorded_at = ?",
                   ((datetime.fromisoformat(started) + timedelta(minutes=5)).isoformat(),))
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=FakeResources(),
        repo_root=tmp_path,
        activity_collector=ExperimentActivityCollector(
            usage_ledger=ledger, capability_store=caps, task_store=tasks
        ),
    )
    result = capturer.capture(experiment_id)
    snapshot = json.loads(store.list_snapshots(experiment_id)[-1]["snapshot_json"])
    assert snapshot["activity"]["inference_usage"]["cost_usd"] == 0.25
    assert snapshot["activity"]["window"]["since"] == started
    metrics = {m["metric_name"]: m["value"] for m in store.list_metrics()}
    assert metrics["inference_cost_usd"] == 0.25 and result["day_index"] == 1

    report = store.build_experiment_report(experiment_id)
    totals = report["activity_totals"]
    assert totals["days_with_activity_evidence"] == 1
    assert totals["inference_usage"]["requests"] == 1
    assert totals["inference_usage"]["cost_usd"] == 0.25
    assert totals["unavailable_sources"] == {}
    assert report["integrity"]["snapshots_verified"] is True


def test_snapshot_without_collector_records_gap_not_zeros(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=FakeResources(),
        repo_root=tmp_path,
    ).capture(experiment_id)
    snapshot = json.loads(store.list_snapshots(experiment_id)[-1]["snapshot_json"])
    assert snapshot["activity"] == {"available": False, "reason": "collector not configured"}
    totals = store.build_experiment_report(experiment_id)["activity_totals"]
    assert totals["days_without_activity_evidence"] == [1]
    assert totals["inference_usage"]["requests"] == 0
    assert totals["days_with_activity_evidence"] == 0


def test_second_day_window_starts_at_previous_snapshot(tmp_path):
    store, experiment_id = create_due_experiment(tmp_path)
    ledger, caps, tasks = stores(tmp_path)
    capturer = ExperimentSnapshotCapturer(
        observatory=store,
        baseline_capture=lambda root: passing_manifest(),
        resource_monitor=FakeResources(),
        repo_root=tmp_path,
        activity_collector=ExperimentActivityCollector(
            usage_ledger=ledger, capability_store=caps, task_store=tasks
        ),
    )
    capturer.capture(experiment_id)
    first = store.list_snapshots(experiment_id)[-1]
    older = (datetime.now(UTC) - timedelta(days=2, hours=1)).isoformat()
    with sqlite3.connect(store.database_path) as db:
        db.execute("UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
                   (older, experiment_id))
    capturer.capture(experiment_id)
    second = json.loads(store.list_snapshots(experiment_id)[-1]["snapshot_json"])
    assert second["day_index"] == 2
    assert second["activity"]["window"]["since"] == first["captured_at"]
