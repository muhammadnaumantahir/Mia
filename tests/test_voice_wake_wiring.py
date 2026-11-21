"""The wake-word routes wired into the real app stay truthful, opt-in and loopback-only."""

import json

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import mia.voice.wake_transport as wake_transport
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.voice.wake import FRAME_BYTES, WakeWordUnavailable

URL = "/api/voice/wake/stream"
FRAME = b"\x01\x00" * (FRAME_BYTES // 2)


class FakeTranscriber:
    def transcribe(self, *_a, **_k):
        return {"text": "", "language": "en", "language_probability": 1.0}


class Detector:
    name = "fake"

    def __init__(self):
        self.closed = 0

    def score(self, frame):
        return 0.95

    def reset(self):
        pass

    def close(self):
        self.closed += 1


def make_app(tmp_path):
    return create_app(
        store=ObservatoryStore(tmp_path / "wake-wiring.sqlite3"),
        transcriber=FakeTranscriber(),
    )


def local_client(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def fake_specs(monkeypatch, present):
    real = wake_transport.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name in present:
            return object()
        if name in {"openwakeword", "onnxruntime", "websockets", "wsproto"}:
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(wake_transport.importlib.util, "find_spec", fake)


def test_routes_are_registered_but_nothing_listens_at_startup(tmp_path):
    app = make_app(tmp_path)
    paths = {getattr(route, "path", None) for route in app.routes}
    assert {URL, "/api/voice/wake/status", "/api/voice/wake/stop"} <= paths
    payload = local_client(app).get("/api/voice/wake/status").json()
    assert payload["session"]["state"] == "off"
    assert payload["session"]["listening"] is False
    assert payload["session"]["audio_retained"] is False


def test_status_requires_a_real_websocket_runtime_not_just_a_route(tmp_path, monkeypatch):
    model = tmp_path / "m.onnx"
    model.write_bytes(b"x")
    monkeypatch.setenv("MIA_WAKE_MODEL_PATH", str(model))
    app = make_app(tmp_path)
    client = local_client(app)

    fake_specs(monkeypatch, present={"openwakeword", "onnxruntime"})  # engine + model, no WS server
    wake = client.get("/api/voice/status").json()["offline_wake_word"]
    assert wake["operational"] is False and wake["reason"] == "audio_transport_not_available"
    assert client.get("/api/voice/wake/status").json()["capability"]["operational"] is False

    fake_specs(monkeypatch, present={"openwakeword", "onnxruntime", "websockets"})
    wake = client.get("/api/voice/status").json()["offline_wake_word"]
    assert wake["operational"] is True and wake["reason"] is None
    assert wake["always_listening"] is False and wake["opt_in_required"] is True
    assert client.get("/api/voice/wake/status").json()["capability"]["operational"] is True


def test_missing_engine_keeps_the_app_not_operational_even_with_routes(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_WAKE_MODEL_PATH", raising=False)
    fake_specs(monkeypatch, present={"websockets"})
    wake = local_client(make_app(tmp_path)).get("/api/voice/status").json()["offline_wake_word"]
    assert wake["operational"] is False and wake["reason"] == "engine_not_installed"


def test_websocket_from_a_non_loopback_client_is_refused(tmp_path):
    app = make_app(tmp_path)
    remote = TestClient(app, client=("203.0.113.7", 50000))
    with pytest.raises(WebSocketDisconnect) as info:
        with remote.websocket_connect(URL):
            pass
    assert info.value.code == 1008
    assert remote.get("/api/voice/wake/status").status_code == 403


def test_start_without_a_model_reports_unavailable_and_stays_off(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_WAKE_MODEL_PATH", raising=False)
    client = local_client(make_app(tmp_path))
    with client.websocket_connect(URL) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_text(json.dumps({"type": "start", "owner_opt_in": True}))
        error = ws.receive_json()
    assert error["code"] == "unavailable" and "MIA_WAKE_MODEL_PATH" in error["message"]
    assert client.get("/api/voice/wake/status").json()["session"]["listening"] is False


def test_full_flow_in_the_real_app_records_metadata_only_events(tmp_path, monkeypatch):
    detector = Detector()
    monkeypatch.setattr(wake_transport, "build_default_detector", lambda: detector)
    store = ObservatoryStore(tmp_path / "flow.sqlite3")
    app = create_app(store=store, transcriber=FakeTranscriber())
    client = local_client(app)
    with client.websocket_connect(URL) as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "start"}))  # no opt-in: must not arm
        assert ws.receive_json()["code"] == "opt_in_required"
        assert client.get("/api/voice/wake/status").json()["session"]["listening"] is False
        ws.send_text(json.dumps({"type": "start", "owner_opt_in": True}))
        assert ws.receive_json()["listening"] is True
        ws.send_bytes(FRAME)
        ws.send_bytes(FRAME)
        assert ws.receive_json()["type"] == "wake"
        assert client.post("/api/voice/wake/stop").json()["listening"] is False
    assert detector.closed == 1
    events = [e for e in store.list_events(limit=50) if e.get("category") == "voice"]
    kinds = {e.get("event_type") for e in events}
    assert {"wake_armed", "wake_detected", "wake_stopped"} <= kinds
    for event in events:
        if str(event.get("event_type", "")).startswith("wake_"):
            payload = json.loads(event["payload_json"])
            assert payload["audio_retained"] is False
            assert all(isinstance(v, (str, int, float, bool, type(None))) for v in payload.values())


def test_arming_is_impossible_over_plain_http(tmp_path):
    client = local_client(make_app(tmp_path))
    for method, path in (("post", "/api/voice/wake/start"), ("post", "/api/voice/wake/arm"),
                         ("put", "/api/voice/wake/status"), ("get", "/api/voice/wake/start")):
        assert getattr(client, method)(path).status_code in (404, 405)


def test_unavailable_error_type_is_what_the_default_factory_raises(monkeypatch):
    monkeypatch.delenv("MIA_WAKE_MODEL_PATH", raising=False)
    with pytest.raises(WakeWordUnavailable):
        wake_transport.build_default_detector()
