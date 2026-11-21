from fastapi.testclient import TestClient

from mia.observatory.api import create_app


def test_code_graph_status_is_available_to_loopback_client():
    client = TestClient(create_app())
    response = client.get("/api/code-graph/status")
    assert response.status_code == 200
    body = response.json()
    assert body["watcher_enabled"] is False
    stored = body["stored_graph"]  # freshness of docs/code-graph/graph.json vs this checkout
    assert stored["state"] in {"missing", "current", "stale", "unreadable"}
    assert isinstance(stored["reasons"], list) and "head" in stored


def test_code_graph_status_denies_remote_client_by_default():
    client = TestClient(create_app(), client=("203.0.113.10", 12345))
    response = client.get("/api/code-graph/status")
    assert response.status_code == 403


def test_forwarded_remote_request_requires_explicit_token(monkeypatch):
    monkeypatch.setenv("MIA_CODE_GRAPH_ALLOW_REMOTE", "true")
    monkeypatch.setenv("MIA_CODE_GRAPH_TOKEN", "a-very-long-owner-generated-token-32chars")
    client = TestClient(create_app(), client=("127.0.0.1", 12345))
    headers = {"X-Forwarded-For": "203.0.113.10"}
    denied = client.get("/api/code-graph/status", headers=headers)
    assert denied.status_code == 403
    allowed = client.get(
        "/api/code-graph/status",
        headers={**headers, "X-Mia-Code-Graph-Token": "a-very-long-owner-generated-token-32chars"},
    )
    assert allowed.status_code == 200



def test_code_graph_impact_endpoint_rejects_option_like_base_reference():
    client = TestClient(create_app())
    response = client.get(
        "/api/code-graph/impact",
        params={"base": "--upload-pack=malicious"},
    )
    assert response.status_code == 400
