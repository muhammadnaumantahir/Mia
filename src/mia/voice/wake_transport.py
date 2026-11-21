"""Loopback-only audio transport for the offline wake word (MIA-VOICE-02).

Carries 16 kHz mono signed 16-bit little-endian PCM from the owner's own
browser to :class:`mia.voice.wake.WakeWordSession` over a WebSocket and returns
small JSON events. It is deliberately strict, because it moves always-on
microphone audio:

* **Loopback only.** The client address must be loopback and the request must
  carry no proxy-forwarding header, so a cloud-hosted or reverse-proxied Mia
  never receives continuous microphone audio. Browsers must also be same-origin
  on a loopback host (cross-site WebSocket and DNS-rebinding protection).
* **Explicit opt-in.** Nothing is armed until the client sends
  ``{"type": "start", "owner_opt_in": true}`` (a strict ``true``).
* **Immediate stop.** A ``stop`` message, ``POST /api/voice/wake/stop``, a socket
  close, an idle stall or any server-side stop all end the session, and audio
  that arrives afterwards is dropped unscored.
* **One session, no audio out.** A single connection at a time; the server never
  echoes audio, and recorded events are metadata only.

Wire protocol v1 is documented in docs/voice/WAKE_WORD_PLAN.md.
"""

from __future__ import annotations

import asyncio
import importlib.util
import ipaddress
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, WebSocket
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from mia.voice.wake import (
    FRAME_SAMPLES,
    SAMPLE_RATE,
    WakeWordSession,
    WakeWordUnavailable,
    build_default_detector,
    offline_wake_word_status,
)

PROTOCOL_VERSION = 1
MAX_CONTROL_MESSAGE_CHARS = 1024
FORWARDING_HEADERS = (
    "forwarded",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
)
LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}

# WebSocket close codes
CLOSE_NORMAL = 1000
CLOSE_UNSUPPORTED = 1003
CLOSE_POLICY = 1008
CLOSE_TOO_BIG = 1009
CLOSE_TRY_LATER = 1013


def websocket_runtime_available() -> bool:
    """True when the ASGI server can actually upgrade WebSocket connections.

    Bare ``uvicorn`` ships no WebSocket implementation; without ``websockets`` or
    ``wsproto`` the stream route would exist but every upgrade would fail. Capability
    reports must be based on this, not on the route being registered.
    """
    for module in ("websockets", "wsproto"):
        try:
            if importlib.util.find_spec(module) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def is_loopback_address(host: str | None) -> bool:
    try:
        return host is not None and ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _headers_are_direct(headers: Any) -> bool:
    return not any(name in headers for name in FORWARDING_HEADERS)


def websocket_origin_allowed(headers: Any) -> bool:
    """Allow no Origin (non-browser local client) or a same-origin loopback page."""
    origin = headers.get("origin")
    if origin is None:
        return True
    parts = urlsplit(origin)
    host_header = (headers.get("host") or "").lower()
    return (
        (parts.hostname or "").lower() in LOOPBACK_HOSTNAMES
        and parts.netloc.lower() == host_header
    )


class WakeTransport:
    """Owns the single wake-word session and serves its WebSocket/REST surface."""

    def __init__(
        self,
        session: WakeWordSession | None = None,
        *,
        record_event: Callable[..., Any] | None = None,
        idle_timeout_seconds: float = 15.0,
    ) -> None:
        self.session = session or WakeWordSession(build_default_detector)
        self._record_event = record_event
        self.idle_timeout_seconds = idle_timeout_seconds
        self._active = False

    # ------------------------------------------------------------------ helpers

    def _record(self, event_type: str, summary: str, outcome: str, **payload: Any) -> None:
        if self._record_event is None:
            return
        try:  # observability must never break the audio path
            self._record_event(
                category="voice",
                event_type=event_type,
                summary=summary,
                outcome=outcome,
                payload={**payload, "audio_retained": False},
            )
        except Exception:
            pass

    @staticmethod
    def _http_guard(request: Request) -> None:
        client = request.client.host if request.client else None
        if not is_loopback_address(client) or not _headers_are_direct(request.headers):
            raise HTTPException(status_code=403, detail="Wake-word control is loopback-only")

    @staticmethod
    async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            pass

    @staticmethod
    async def _close(websocket: WebSocket, code: int = CLOSE_NORMAL) -> None:
        try:
            await websocket.close(code=code)
        except (RuntimeError, WebSocketDisconnect):
            pass

    def _receive_timeout(self) -> float:
        status = self.session.status()
        if not status["listening"]:
            return self.idle_timeout_seconds
        remaining = status["max_armed_seconds"] - status["armed_for_seconds"]
        return max(0.01, min(self.idle_timeout_seconds, remaining))

    async def _notify_stopped(self, websocket: WebSocket) -> None:
        status = self.session.status()
        self._record(
            "wake_stopped",
            "Offline wake-word listening stopped",
            "stopped",
            reason=status["stop_reason"],
            activations=status["activations"],
        )
        await self._send(websocket, {"type": "stopped", **status})
        await self._close(websocket)

    # ---------------------------------------------------------------- websocket

    async def stream(self, websocket: WebSocket) -> None:
        client = websocket.client.host if websocket.client else None
        if (
            not is_loopback_address(client)
            or not _headers_are_direct(websocket.headers)
            or not websocket_origin_allowed(websocket.headers)
        ):
            await websocket.close(code=CLOSE_POLICY)
            return
        if self._active:
            await websocket.close(code=CLOSE_TRY_LATER)
            return
        self._active = True
        try:
            await websocket.accept()
            await self._serve(websocket)
        except WebSocketDisconnect:
            pass
        finally:
            self._active = False
            # Any exit path - close, error, cancellation - releases the detector.
            self.session.stop("transport_closed")

    async def _serve(self, websocket: WebSocket) -> None:
        await self._send(
            websocket,
            {
                "type": "ready",
                "protocol": PROTOCOL_VERSION,
                "encoding": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
                "frame_samples": FRAME_SAMPLES,
                "opt_in_required": True,
            },
        )
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=self._receive_timeout()
                )
            except TimeoutError:
                if self.session.state == WakeWordSession.ARMED:
                    if not self.session.expire_if_due():
                        self.session.stop("audio_stalled")
                    await self._notify_stopped(websocket)
                else:
                    await self._close(websocket)
                return
            if message["type"] == "websocket.disconnect":
                return
            if message.get("text") is not None:
                if not await self._on_control(websocket, message["text"]):
                    return
            elif message.get("bytes") is not None:
                if not await self._on_audio(websocket, message["bytes"]):
                    return

    async def _on_control(self, websocket: WebSocket, text: str) -> bool:
        """Handle a control message. Returns False when the connection should end."""
        if len(text) > MAX_CONTROL_MESSAGE_CHARS:
            await self._send(websocket, {"type": "error", "code": "message_too_large"})
            await self._close(websocket, CLOSE_TOO_BIG)
            return False
        try:
            command = json.loads(text)
            kind = command["type"]
        except (ValueError, KeyError, TypeError):
            await self._send(websocket, {"type": "error", "code": "bad_message"})
            return True

        if kind == "start":
            if command.get("owner_opt_in") is not True:
                await self._send(websocket, {"type": "error", "code": "opt_in_required"})
                return True
            try:
                status = await run_in_threadpool(self.session.arm, owner_opt_in=True)
            except WakeWordUnavailable as exc:
                await self._send(
                    websocket, {"type": "error", "code": "unavailable", "message": str(exc)}
                )
                await self._close(websocket)
                return False
            self._record(
                "wake_armed",
                "Offline wake-word listening started by explicit owner opt-in",
                "armed",
                label=status["label"],
                max_armed_seconds=status["max_armed_seconds"],
            )
            await self._send(websocket, {"type": "status", **status})
            return True
        if kind == "stop":
            self.session.stop("owner")
            await self._notify_stopped(websocket)
            return False
        if kind == "status":
            await self._send(websocket, {"type": "status", **self.session.status()})
            return True
        await self._send(websocket, {"type": "error", "code": "unknown_type"})
        return True

    async def _on_audio(self, websocket: WebSocket, data: bytes) -> bool:
        state = self.session.state
        if state == WakeWordSession.STOPPED:
            # Stopped elsewhere (REST stop, time limit, storm guard): tell the page, drop audio.
            await self._notify_stopped(websocket)
            return False
        if state != WakeWordSession.ARMED:
            await self._send(websocket, {"type": "error", "code": "audio_before_start"})
            await self._close(websocket, CLOSE_POLICY)
            return False
        try:
            event = await run_in_threadpool(self.session.feed, data)
        except ValueError:
            self.session.stop("bad_audio")
            await self._send(websocket, {"type": "error", "code": "bad_audio"})
            await self._close(websocket, CLOSE_UNSUPPORTED)
            return False
        if event is not None:
            self._record(
                "wake_detected",
                "Offline wake word detected",
                "detected",
                label=event.label,
                score=round(event.score, 3),
            )
            await self._send(
                websocket,
                {"type": "wake", "label": event.label, "score": round(event.score, 3)},
            )
        if self.session.state != WakeWordSession.ARMED:
            await self._notify_stopped(websocket)
            return False
        return True

    # --------------------------------------------------------------------- REST

    def status_payload(self) -> dict[str, Any]:
        return {
            "session": self.session.status(),
            "capability": offline_wake_word_status(transport_available=websocket_runtime_available()),
            "protocol": PROTOCOL_VERSION,
        }


def register_wake_routes(
    app: FastAPI,
    transport: WakeTransport | None = None,
    *,
    record_event: Callable[..., Any] | None = None,
) -> WakeTransport:
    """Attach the wake-word routes (called by ``create_app``; arming still needs owner opt-in)."""
    transport = transport or WakeTransport(record_event=record_event)

    @app.websocket("/api/voice/wake/stream")
    async def wake_stream(websocket: WebSocket) -> None:
        await transport.stream(websocket)

    @app.get("/api/voice/wake/status")
    def wake_status(request: Request) -> dict[str, Any]:
        transport._http_guard(request)
        return transport.status_payload()

    @app.post("/api/voice/wake/stop")
    def wake_stop(request: Request) -> dict[str, Any]:
        transport._http_guard(request)
        status = transport.session.stop("owner")
        transport._record(
            "wake_stopped",
            "Offline wake-word listening stopped by owner request",
            "stopped",
            reason=status["stop_reason"],
            activations=status["activations"],
        )
        return status

    return transport
