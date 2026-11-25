"""MIA-COMP-01: loopback Host/Origin guard (DNS rebinding and cross-site request defence)."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.security.local_guard import host_is_loopback, origin_is_same_origin


@pytest.fixture
def guarded(tmp_path):
    app = create_app(store=ObservatoryStore(tmp_path / "g.sqlite3"), local_guard=True)
    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_helpers():
    assert host_is_loopback("127.0.0.1:8000") and host_is_loopback("localhost") and host_is_loopback("[::1]:80")
    assert not host_is_loopback("evil.example") and not host_is_loopback(None) and not host_is_loopback("127.0.0.1.evil.example")
    assert origin_is_same_origin("http://127.0.0.1:8000", "127.0.0.1:8000")
    assert not origin_is_same_origin("http://127.0.0.1:9999", "127.0.0.1:8000")
    assert not origin_is_same_origin("http://localhost:8000", "127.0.0.1:8000")
    assert not origin_is_same_origin("null", "127.0.0.1:8000")
    assert not origin_is_same_origin("https://evil.example", "127.0.0.1:8000")


def test_rebound_host_is_rejected_for_reads_too(guarded):
    r = guarded.get("/api/voice/status", headers={"Host": "evil.example"})
    assert r.status_code == 400


def test_loopback_reads_and_clients_without_origin_work(guarded):
    assert guarded.get("/api/voice/status").status_code == 200
    assert guarded.get("/api/voice/status", headers={"Origin": "https://evil.example"}).status_code == 200  # reads: no state change
    assert guarded.post("/api/voice/speak", json={"text": ""}).status_code != 403


def test_cross_origin_mutations_are_refused(guarded):
    for origin in ("https://evil.example", "null", "http://127.0.0.1:9999"):
        r = guarded.post("/api/voice/speak", json={"text": "hi"}, headers={"Origin": origin})
        assert r.status_code == 403, origin
    r = guarded.post("/api/voice/speak", json={"text": "hi"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_mutation_passes_the_guard(guarded):
    r = guarded.post("/api/voice/speak", json={"text": "hi"}, headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code != 403


def test_websocket_foreign_origin_is_closed(guarded):
    with pytest.raises(WebSocketDisconnect) as exc:
        with guarded.websocket_connect("/api/voice/wake/stream", headers={"Origin": "https://evil.example"}):
            pass
    assert exc.value.code == 1008


def test_remote_companion_sync_is_tls_allowlist_and_device_token_gated(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_REMOTE_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MIA_REMOTE_ALLOWED_HOSTS", "mia.example.test")
    app = create_app(store=ObservatoryStore(tmp_path / "remote.sqlite3"), local_guard=True)
    client = TestClient(app, base_url="https://mia.example.test")
    token = {"Authorization": "Bearer mia-dev_not-a-real-token"}

    # The guard permits only the TLS/allowlisted/device-token route to reach the handler;
    # the handler then rejects this fake token with 401.
    allowed_to_handler = client.get("/api/companion/sync/pull", headers=token)
    assert allowed_to_handler.status_code == 401

    assert client.get("/api/companion/sync/pull").status_code == 400
    assert client.get(
        "/api/companion/sync/pull",
        headers={**token, "Origin": "https://mia.example.test"},
    ).status_code == 400
    assert client.post("/api/companion/pair", json={}, headers=token).status_code == 400
    assert client.get("/api/companion-admin/devices", headers=token).status_code == 403

    insecure = TestClient(app, base_url="http://mia.example.test")
    assert insecure.get("/api/companion/sync/pull", headers=token).status_code == 400
    wrong_host = TestClient(app, base_url="https://other.example.test")
    assert wrong_host.get("/api/companion/sync/pull", headers=token).status_code == 400


def test_remote_companion_access_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_REMOTE_ACCESS_ENABLED", raising=False)
    monkeypatch.delenv("MIA_REMOTE_ALLOWED_HOSTS", raising=False)
    app = create_app(store=ObservatoryStore(tmp_path / "remote-off.sqlite3"), local_guard=True)
    client = TestClient(app, base_url="https://mia.example.test")
    assert client.get(
        "/api/companion/sync/pull",
        headers={"Host": "mia.example.test", "Authorization": "Bearer mia-dev_fake"},
    ).status_code == 400


def test_guard_is_off_by_default(tmp_path):
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3")))
    assert client.get("/api/voice/status", headers={"Host": "evil.example"}).status_code == 200
