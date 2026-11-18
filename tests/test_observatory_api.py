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
