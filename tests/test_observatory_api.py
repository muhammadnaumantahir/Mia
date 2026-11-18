from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_observatory_health_dashboard_and_event_api(tmp_path):
    store = ObservatoryStore(tmp_path / "api.sqlite3")
    client = TestClient(create_app(store))

    assert client.get("/health").json()["status"] == "ok"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Evolution Observatory" in dashboard.text

    created = client.post(
        "/api/observatory/events",
        json={
            "category": "validation",
            "event_type": "test",
            "summary": "API test event",
            "day_index": 0,
            "layer_id": "SPS-09",
            "payload": {"valid": True},
        },
    )
    assert created.status_code == 201
    listed = client.get("/api/observatory/events").json()["items"]
    assert listed[0]["summary"] == "API test event"


def test_observatory_metric_api_returns_real_samples(tmp_path):
    store = ObservatoryStore(tmp_path / "api.sqlite3")
    client = TestClient(create_app(store))
    response = client.post(
        "/api/observatory/metrics",
        json={
            "day_index": 0,
            "metric_name": "task_success_rate",
            "value": 75.0,
            "unit": "percent",
            "benchmark_id": "tasks-v1",
        },
    )
    assert response.status_code == 201
    metrics = client.get(
        "/api/observatory/metrics",
        params={"metric_name": "task_success_rate"},
    ).json()["items"]
    assert len(metrics) == 1
    assert metrics[0]["value"] == 75.0


def _report_manifest():
    return {
        "source_commit": "0123456789abcdef0123456789abcdef01234567",
        "configuration_hash": "sha256:" + "a" * 64,
        "benchmark_suite_id": "mia-foundation-v2",
        "environment": {
            "os": "Windows",
            "python": "3.13",
            "platform": "Windows test host",
            "machine": "AMD64",
        },
        "fixed_benchmark_results": {
            "tests": 6,
            "passed": 6,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": 1.25,
            "pytest_exit_code": 0,
            "suite_id": "mia-foundation-v2",
        },
        "baseline_results": {
            "tests": 10,
            "passed": 10,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "ruff_passed": True,
            "ruff_exit_code": 0,
            "pytest_exit_code": 0,
            "capture_method": "local-host-ruff-pytest-v2",
            "capture_host": "report-test-host",
        },
        "verification_passed": True,
    }


def test_completed_experiment_report_compares_verified_day_zero_and_final(tmp_path):
    store = ObservatoryStore(tmp_path / "report.sqlite3")
    prepared = store.prepare_experiment(
        name="Report test",
        baseline_manifest=_report_manifest(),
        planned_days=1,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)
    store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=1,
        source_commit="1123456789abcdef0123456789abcdef01234567",
        snapshot={
            "day_index": 1,
            "source_commit": "1123456789abcdef0123456789abcdef01234567",
            "verification": {
                "tests": 12,
                "passed": 12,
                "failures": 0,
                "errors": 0,
                "skipped": 0,
                "ruff_passed": True,
            },
            "resources": {"cpu_percent": 18.0, "memory_percent": 42.0},
            "verification_passed": True,
        },
    )
    store.record_event(
        category="experiment",
        event_type="daily_snapshot_captured",
        summary="Verified Day 1 captured",
        experiment_id=experiment_id,
        day_index=1,
    )
    store.record_metric(
        experiment_id=experiment_id,
        day_index=1,
        metric_name="test_pass_rate",
        value=100.0,
        unit="percent",
        benchmark_id="mia-foundation-v2",
    )

    client = TestClient(create_app(store))
    response = client.get(f"/api/experiments/{experiment_id}/report")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["experiment"]["conclusion"] == "completed"
    assert report["report_ready"] is True
    assert report["days"]["recorded"] == [0, 1]
    assert report["days"]["missing"] == []
    assert report["comparison"]["baseline_test_pass_rate"] == 100.0
    assert report["comparison"]["latest_or_final_test_pass_rate"] == 100.0
    assert report["comparison"]["test_pass_rate_change_points"] == 0.0
    assert report["comparison"]["comparison_is_final"] is True
    assert report["integrity"]["baseline_verified"] is True
    assert report["integrity"]["snapshots_verified"] is True
    assert len(report["metrics"]) == 1
    assert report["capability_attribution"]["available"] is False


def test_incomplete_experiment_report_never_claims_completion(tmp_path):
    from datetime import UTC, datetime, timedelta
    import sqlite3

    store = ObservatoryStore(tmp_path / "incomplete-report.sqlite3")
    prepared = store.prepare_experiment(
        name="Incomplete report test",
        baseline_manifest=_report_manifest(),
        planned_days=1,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)
    started_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE experiments SET started_at = ? WHERE experiment_id = ?",
            (started_at, experiment_id),
        )
        db.commit()

    client = TestClient(create_app(store))
    response = client.get(f"/api/experiments/{experiment_id}/report")

    assert response.status_code == 200, response.text
    report = response.json()
    assert report["experiment"]["status"] == "incomplete"
    assert report["experiment"]["conclusion"] == "incomplete"
    assert report["report_ready"] is True
    assert report["days"]["recorded"] == [0]
    assert report["days"]["missing"] == [1]
    assert report["days"]["unavailable"] == []
    assert report["days"]["unaccounted"] == [1]
    assert report["comparison"]["comparison_is_final"] is False
    assert report["comparison"]["compared_day"] is None
    assert report["comparison"]["latest_or_final_tests"] == {}
    assert report["comparison"]["latest_or_final_test_pass_rate"] is None


def test_report_api_rejects_tampered_snapshot_evidence(tmp_path):
    import sqlite3

    store = ObservatoryStore(tmp_path / "tampered-report.sqlite3")
    prepared = store.prepare_experiment(
        name="Tampered report",
        baseline_manifest=_report_manifest(),
        planned_days=1,
    )
    experiment_id = prepared["experiment_id"]
    store.start_experiment(experiment_id)
    store.record_daily_snapshot(
        experiment_id=experiment_id,
        day_index=1,
        source_commit="1123456789abcdef0123456789abcdef01234567",
        snapshot={
            "day_index": 1,
            "source_commit": "1123456789abcdef0123456789abcdef01234567",
            "verification": {"tests": 1, "passed": 1},
            "verification_passed": True,
        },
    )
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE daily_snapshots SET snapshot_json = ? WHERE experiment_id = ?",
            ('{"tampered": true}', experiment_id),
        )
        db.commit()

    client = TestClient(create_app(store))
    response = client.get(f"/api/experiments/{experiment_id}/report")

    assert response.status_code == 500
    assert "snapshot hash" in response.json()["detail"]


def test_report_api_rejects_tampered_day_zero_baseline(tmp_path):
    import sqlite3

    store = ObservatoryStore(tmp_path / "tampered-day-zero.sqlite3")
    prepared = store.prepare_experiment(
        name="Tampered baseline report",
        baseline_manifest=_report_manifest(),
        planned_days=1,
    )
    experiment_id = prepared["experiment_id"]
    with sqlite3.connect(store.database_path) as db:
        db.execute(
            "UPDATE experiment_baselines SET manifest_json = ? WHERE experiment_id = ?",
            ('{"tampered": true}', experiment_id),
        )
        db.commit()

    client = TestClient(create_app(store))
    response = client.get(f"/api/experiments/{experiment_id}/report")

    assert response.status_code == 500
    assert "baseline hash" in response.json()["detail"]
