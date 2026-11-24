"""Read-only evidence API behind the observatory dashboard (MIA-OBS-06)."""

import json

from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.benchmarks import record_run
from mia.observatory.store import ObservatoryStore
from tests.test_experiment_scheduler import passing_manifest

BASE = passing_manifest()["source_commit"]


def seeded(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    eid = store.prepare_experiment(name="Evidence run", baseline_manifest=passing_manifest(),
                                   planned_days=5)["experiment_id"]
    store.start_experiment(eid)
    for day, commit, value in ((1, BASE, 10.0), (2, "newcommit", 30.0)):
        store.record_daily_snapshot(experiment_id=eid, day_index=day, source_commit=commit, snapshot={
            "day_index": day, "source_commit": commit, "missing_day_indices": [],
            "activity": {"capabilities": {"available": True, "transitions": {},
                                           "owner_approved_actions": 0}}})
        store.record_metric(day_index=day, metric_name="cpu_percent", value=value, unit="percent",
                            experiment_id=eid)
    run = {"suite_id": "mia-scripted-memory", "suite_version": "sha256:v1",
           "evidence": "deterministic_scripted_provider",
           "conditions": {"models": [], "commit": BASE}, "passed": 4, "total": 4,
           "items": {f"s{i}": True for i in range(4)}}
    event_id = record_run(store, run, day_index=1, experiment_id=eid)
    return store, TestClient(create_app(store)), eid, event_id


def test_unknown_ids_are_404_and_nothing_is_invented(tmp_path):
    store = ObservatoryStore(tmp_path / "o.sqlite3")
    client = TestClient(create_app(store))
    for url in ("/api/experiments/nope/evidence", "/api/observatory/events/nope",
                "/api/observatory/metrics/nope"):
        assert client.get(url).status_code == 404
    assert store.list_metrics() == [] and store.list_events() == []


def test_evidence_report_is_served_with_markdown_and_matches_the_generator(tmp_path):
    store, client, eid, _ = seeded(tmp_path)
    body = client.get(f"/api/experiments/{eid}/evidence").json()
    assert body["overview"]["name"] == "Evidence run"
    assert body["coverage"]["captured_days"] == [1, 2] and body["coverage"]["missing_days"] == [3, 4, 5]
    assert body["report_sha256"] in body["markdown"]
    from mia.observatory.report_generator import generate_report
    assert body["report_sha256"] == generate_report(store, eid)["report_sha256"]


def test_serving_the_report_writes_nothing(tmp_path):
    store, client, eid, _ = seeded(tmp_path)
    before = (len(store.list_events(limit=1000)), len(store.list_metrics()))
    for _ in range(2):
        assert client.get(f"/api/experiments/{eid}/evidence").status_code == 200
    assert (len(store.list_events(limit=1000)), len(store.list_metrics())) == before


def test_every_metric_row_links_to_the_raw_samples_it_was_computed_from(tmp_path):
    store, client, eid, _ = seeded(tmp_path)
    (row,) = client.get(f"/api/experiments/{eid}/evidence").json()["metric_changes"]["cpu_percent"]
    first = client.get(f"/api/observatory/metrics/{row['from_sample_id']}").json()
    second = client.get(f"/api/observatory/metrics/{row['to_sample_id']}").json()
    assert (first["value"], first["day_index"]) == (row["from_value"], 1) == (10.0, 1)
    assert (second["value"], second["day_index"]) == (row["to_value"], 2) == (30.0, 2)
    assert json.loads(first["context_json"])["definition_version"] == 1  # version metadata present


def test_benchmark_runs_link_to_the_stored_event(tmp_path):
    _, client, eid, event_id = seeded(tmp_path)
    run = client.get(f"/api/experiments/{eid}/evidence").json()["benchmarks"][
        "mia-scripted-memory"]["runs"][0]
    assert run["event_id"] == event_id
    event = client.get(f"/api/observatory/events/{event_id}").json()
    assert event["event_type"] == "benchmark_run"
    assert json.loads(event["payload_json"])["suite_version"] == "sha256:v1"
