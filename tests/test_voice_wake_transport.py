"""Tests for the loopback-only wake-word audio transport (no hardware, fake detector)."""

import asyncio
import json
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mia.voice.wake import FRAME_BYTES, WakeWordConfig, WakeWordSession, WakeWordUnavailable
from mia.voice.wake_transport import (
    CLOSE_POLICY,
    CLOSE_TRY_LATER,
    WakeTransport,
    is_loopback_address,
    register_wake_routes,
    websocket_origin_allowed,
)

FRAME = b"\x01\x00" * (FRAME_BYTES // 2)
URL = "/api/voice/wake/stream"
SAME_ORIGIN = {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}


class Detector:
    name = "scripted"

    def __init__(self, default=0.0):
        self.default = default
        self.scored = 0
        self.closed = 0
        self.resets = 0

    def score(self, frame):
        self.scored += 1
        return self.default

    def reset(self):
        self.resets += 1

    def close(self):
        self.closed += 1


class Clock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


def build(detector=None, *, client_host="127.0.0.1", idle=15.0, config=None, factory=None):
    detector = detector or Detector()
    clock = Clock()
    calls = {"n": 0}

    def default_factory():
        calls["n"] += 1
        return detector

    session = WakeWordSession(factory or default_factory, config or WakeWordConfig(patience=2), clock=clock)
    events = []
    transport = WakeTransport(
        session, record_event=lambda **kw: events.append(kw), idle_timeout_seconds=idle
    )
    app = FastAPI()
    register_wake_routes(app, transport)
    client = TestClient(app, client=(client_host, 50000))
    return client, transport, detector, events, clock, calls


def start(ws, **extra):
    ws.send_text(json.dumps({"type": "start", "owner_opt_in": True, **extra}))
    return ws.receive_json()


def expect_close(client, code, headers=None):
    with pytest.raises(WebSocketDisconnect) as info:
        with client.websocket_connect(URL, headers=headers or SAME_ORIGIN):
            pass
    assert info.value.code == code


# ----------------------------------------------------------------------- guards


def test_loopback_and_origin_helpers():
    assert is_loopback_address("127.0.0.1") and is_loopback_address("::1")
    assert not is_loopback_address("203.0.113.9")
    assert not is_loopback_address("testclient") and not is_loopback_address(None)
    assert websocket_origin_allowed({})  # no Origin: non-browser local client
    assert websocket_origin_allowed({"origin": "http://localhost:8765", "host": "localhost:8765"})
    assert websocket_origin_allowed({"origin": "http://[::1]:8765", "host": "[::1]:8765"})
    assert not websocket_origin_allowed({"origin": "https://evil.example", "host": "127.0.0.1:8765"})
    # DNS rebinding: same-origin to the Host header, but not a loopback host name.
    assert not websocket_origin_allowed(
        {"origin": "http://evil.example:8765", "host": "evil.example:8765"}
    )
    # A different local page/port is not same-origin.
    assert not websocket_origin_allowed(
        {"origin": "http://127.0.0.1:3000", "host": "127.0.0.1:8765"}
    )


def test_non_loopback_client_is_rejected_and_never_builds_a_detector():
    client, _, _, _, _, calls = build(client_host="203.0.113.9")
    expect_close(client, CLOSE_POLICY)
    assert calls["n"] == 0


@pytest.mark.parametrize("header", ["x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host"])
def test_proxied_requests_are_rejected_even_from_a_loopback_proxy(header):
    client, _, _, _, _, calls = build()
    expect_close(client, CLOSE_POLICY, {**SAME_ORIGIN, header: "203.0.113.9"})
    assert calls["n"] == 0


@pytest.mark.parametrize(
    "headers",
    [
        {"host": "127.0.0.1:8765", "origin": "https://evil.example"},
        {"host": "evil.example:8765", "origin": "http://evil.example:8765"},
        {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:3000"},
    ],
)
def test_cross_site_and_rebinding_origins_are_rejected(headers):
    client, _, _, _, _, calls = build()
    expect_close(client, CLOSE_POLICY, headers)
    assert calls["n"] == 0


def test_same_origin_page_and_origin_less_local_client_are_accepted():
    client, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        assert ws.receive_json()["type"] == "ready"
    with client.websocket_connect(URL) as ws:
        ready = ws.receive_json()
    assert ready["encoding"] == "pcm_s16le"
    assert ready["sample_rate"] == 16000 and ready["frame_samples"] == 1280
    assert ready["opt_in_required"] is True


# ------------------------------------------------------------- opt-in and streaming


@pytest.mark.parametrize("payload", [{"type": "start"}, {"type": "start", "owner_opt_in": False},
                                     {"type": "start", "owner_opt_in": "true"},
                                     {"type": "start", "owner_opt_in": 1}])
def test_connecting_or_starting_without_a_strict_opt_in_never_arms(payload):
    client, transport, _, _, _, calls = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        ws.send_text(json.dumps(payload))
        assert ws.receive_json() == {"type": "error", "code": "opt_in_required"}
        assert transport.session.state == "off"
    assert calls["n"] == 0


def test_start_arms_then_a_wake_event_is_returned_without_any_audio():
    client, transport, detector, events, _, _ = build(Detector(default=0.9))
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        status = start(ws)
        assert status["type"] == "status" and status["listening"] is True
        ws.send_bytes(FRAME)
        ws.send_bytes(FRAME)
        wake = ws.receive_json()
        assert wake["type"] == "wake" and wake["label"] == "Hey Mia"
        assert wake["score"] == pytest.approx(0.9)
        assert set(wake) == {"type", "label", "score"}  # nothing resembling audio
    kinds = [e["event_type"] for e in events]
    assert "wake_armed" in kinds and "wake_detected" in kinds
    for event in events:  # observability is metadata only
        assert event["payload"]["audio_retained"] is False
        assert not any(isinstance(v, (bytes, bytearray)) for v in event["payload"].values())


def test_unknown_and_malformed_control_messages_are_rejected_without_arming():
    client, transport, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        ws.send_text("not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "dance"}))
        assert ws.receive_json()["code"] == "unknown_type"
        ws.send_text(json.dumps({"type": "status"}))
        assert ws.receive_json()["listening"] is False
    assert transport.session.state == "off"


def test_oversized_control_message_closes_the_connection():
    client, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        ws.send_text("x" * 2000)
        assert ws.receive_json()["code"] == "message_too_large"


def test_engine_unavailable_is_reported_and_session_stays_off():
    def missing():
        raise WakeWordUnavailable("No wake-word model configured.")

    client, transport, *_ = build(factory=missing)
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "start", "owner_opt_in": True}))
        error = ws.receive_json()
    assert error["code"] == "unavailable" and "model" in error["message"]
    assert transport.session.state == "off"


def test_audio_before_start_closes_with_a_policy_error():
    client, _, detector, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        ws.send_bytes(FRAME)
        assert ws.receive_json()["code"] == "audio_before_start"
    assert detector.scored == 0


# --------------------------------------------------------------------- stopping


def test_stop_message_stops_the_session_releases_the_detector_and_closes():
    client, transport, detector, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        ws.send_text(json.dumps({"type": "stop"}))
        stopped = ws.receive_json()
    assert stopped["type"] == "stopped" and stopped["stop_reason"] == "owner"
    assert stopped["listening"] is False
    assert detector.closed == 1
    assert transport.session.state == "stopped"


def test_dropping_the_socket_without_stop_still_stops_listening():
    client, transport, detector, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
    deadline = time.time() + 3
    while transport.session.state == "armed" and time.time() < deadline:
        time.sleep(0.01)
    assert transport.session.state == "stopped"
    assert transport.session.status()["stop_reason"] == "transport_closed"
    assert detector.closed == 1


def test_rest_stop_is_immediate_and_later_audio_is_dropped_with_a_notice():
    client, transport, detector, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        response = client.post("/api/voice/wake/stop")
        assert response.status_code == 200
        assert response.json()["listening"] is False
        # Guaranteed by the session even before the page notices:
        assert transport.session.state == "stopped"
        ws.send_bytes(FRAME)
        notice = ws.receive_json()
    assert notice["type"] == "stopped" and notice["stop_reason"] == "owner"
    assert detector.scored == 0  # the late frame was never scored
    assert detector.closed == 1


def test_bad_audio_stops_the_session_and_closes():
    client, transport, detector, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        ws.send_bytes(b"\x00\x00\x00")  # not whole 16-bit samples
        assert ws.receive_json()["code"] == "bad_audio"
    assert transport.session.status()["stop_reason"] == "bad_audio"
    assert detector.closed == 1


def test_only_one_connection_at_a_time():
    client, *_ = build()
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as first:
        first.receive_json()
        with pytest.raises(WebSocketDisconnect) as info:
            with client.websocket_connect(URL, headers=SAME_ORIGIN):
                pass
        assert info.value.code == CLOSE_TRY_LATER
        first.send_text(json.dumps({"type": "status"}))
        assert first.receive_json()["type"] == "status"  # first connection unaffected


def test_a_stalled_microphone_stops_listening():
    client, transport, detector, *_ = build(idle=0.2)
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        stopped = ws.receive_json()  # no audio is sent
    assert stopped["type"] == "stopped" and stopped["stop_reason"] == "audio_stalled"
    assert detector.closed == 1


def test_time_limit_is_enforced_even_when_no_audio_arrives():
    client, transport, detector, _, clock, _ = build(
        idle=0.2, config=WakeWordConfig(max_armed_seconds=60.0)
    )
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        clock.now += 61.0
        stopped = ws.receive_json()
    assert stopped["stop_reason"] == "max_armed_duration"
    assert detector.closed == 1


def test_idle_connection_that_never_starts_is_closed():
    client, transport, *_ = build(idle=0.2)
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert transport.session.state == "off"


def test_server_side_stop_during_streaming_notifies_the_page():
    # An activation storm stops the session server-side; the page must be told.
    detector = Detector(default=0.99)
    config = WakeWordConfig(patience=1, cooldown_seconds=0.0, max_activations_per_minute=1)
    client, transport, *_ = build(detector, config=config)
    with client.websocket_connect(URL, headers=SAME_ORIGIN) as ws:
        ws.receive_json()
        start(ws)
        ws.send_bytes(FRAME)
        assert ws.receive_json()["type"] == "wake"
        ws.send_bytes(FRAME)
        stopped = ws.receive_json()
    assert stopped["type"] == "stopped" and stopped["stop_reason"] == "too_many_activations"


# ------------------------------------------------------------------------- REST


def test_rest_endpoints_are_loopback_only():
    remote, *_ = build(client_host="203.0.113.9")
    assert remote.get("/api/voice/wake/status").status_code == 403
    assert remote.post("/api/voice/wake/stop").status_code == 403
    local, *_ = build()
    for header in ("x-forwarded-for", "forwarded", "x-real-ip"):
        assert local.get("/api/voice/wake/status", headers={header: "203.0.113.9"}).status_code == 403
        assert local.post("/api/voice/wake/stop", headers={header: "203.0.113.9"}).status_code == 403


def test_status_endpoint_reports_session_and_capability_but_no_arming_route_exists():
    client, *_ = build()
    payload = client.get("/api/voice/wake/status").json()
    assert payload["session"]["listening"] is False
    assert payload["session"]["audio_retained"] is False
    assert payload["capability"]["audio_transport_available"] is True
    assert payload["protocol"] == 1
    # Arming is only possible through the WebSocket opt-in message.
    assert client.post("/api/voice/wake/start").status_code in (404, 405)
    assert client.post("/api/voice/wake/arm").status_code in (404, 405)


# --------------------------------------------------- real uvicorn + real WS client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_end_to_end_over_a_real_uvicorn_server_and_websocket_client():
    uvicorn = pytest.importorskip("uvicorn")
    websockets = pytest.importorskip("websockets")
    from websockets.asyncio.client import connect
    from websockets.exceptions import InvalidStatus

    detector = Detector(default=0.9)
    session = WakeWordSession(lambda: detector, WakeWordConfig(patience=2))
    app = FastAPI()
    register_wake_routes(app, WakeTransport(session))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", ws="websockets")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"

    async def run():
        url = f"ws://127.0.0.1:{port}{URL}"
        async with connect(url) as ws:
            assert json.loads(await ws.recv())["type"] == "ready"
            await ws.send(json.dumps({"type": "start", "owner_opt_in": True}))
            assert json.loads(await ws.recv())["listening"] is True
            await ws.send(FRAME)
            await ws.send(FRAME)
            wake = json.loads(await ws.recv())
            assert wake["type"] == "wake"
            await ws.send(json.dumps({"type": "stop"}))
            assert json.loads(await ws.recv())["type"] == "stopped"
        # A browser from another site is refused at the upgrade, before any audio.
        with pytest.raises(InvalidStatus) as info:
            async with connect(url, origin="https://evil.example"):
                pass
        assert info.value.response.status_code == 403

    try:
        asyncio.run(run())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert detector.closed == 1
    assert websockets is not None
