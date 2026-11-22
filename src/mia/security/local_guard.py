"""Loopback-only request guard: blocks DNS rebinding and cross-site requests (MIA-COMP-01).

Mia's API has no login. Its safety assumption is that only the owner's own browser and tools
reach it over loopback. Without this guard that assumption fails in two well-known ways:

* DNS rebinding: a web page makes the owner's browser send requests for ``evil.example`` to
  ``127.0.0.1``; the server answers because it never looked at the ``Host`` header.
* Cross-site requests: a page the owner visits posts to ``http://127.0.0.1:<port>/...`` or opens a
  WebSocket to it. Body-less POSTs such as pause or stop-all need no CORS preflight.

Rules (HTTP and WebSocket):

* ``Host`` must name loopback (``127.0.0.1``, ``localhost`` or ``::1``), else 400 / close 1008.
* For state-changing methods and WebSockets: ``Origin``, when present, must be exactly the
  same origin as the request (same loopback host and port); ``null`` and foreign origins get 403.
  ``Sec-Fetch-Site: cross-site`` is also refused. Requests without ``Origin`` (curl, scripts, the
  supervisor demo) are allowed, because a browser always sends ``Origin`` on cross-site POSTs.

This is transport hygiene, not authorization: any local process can still call the API. Who may
approve what is the permission broker's job (MIA-COMP-04).
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def split_host(value: str) -> tuple[str, str]:
    """Return (hostname, port) from a Host header value; both lower-cased, port may be ''."""
    value = value.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return "", ""
        return value[1:end], value[end + 2 :] if value[end + 1 : end + 2] == ":" else ""
    if value.count(":") == 1:
        name, port = value.split(":", 1)
        return name.rstrip("."), port
    return value.rstrip("."), ""


def host_is_loopback(value: str | None) -> bool:
    return bool(value) and split_host(value or "")[0] in LOOPBACK_HOSTS


def origin_is_same_origin(origin: str, host: str) -> bool:
    """True when ``origin`` is the loopback origin that matches this request's Host header."""
    if origin.strip().lower() == "null":
        return False
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    default = "443" if parts.scheme == "https" else "80"
    origin_host = parts.hostname.lower().rstrip(".")
    origin_port = str(parts.port) if parts.port else default
    host_name, host_port = split_host(host)
    host_port = host_port or default
    return (
        origin_host in LOOPBACK_HOSTS
        and origin_host == host_name
        and origin_port == host_port
    )


class LoopbackGuard:
    """Pure ASGI middleware so it covers HTTP and WebSocket scopes alike."""

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = headers.get("host")
        if not host_is_loopback(host):
            await self._reject(scope, send, 400, "Invalid host header")
            return
        unsafe = scope["type"] == "websocket" or scope.get("method", "GET").upper() not in _SAFE_METHODS
        if unsafe:
            origin = headers.get("origin")
            if headers.get("sec-fetch-site", "").lower() == "cross-site":
                await self._reject(scope, send, 403, "Cross-site requests are not allowed")
                return
            if origin is not None and not origin_is_same_origin(origin, host or ""):
                await self._reject(scope, send, 403, "Cross-origin requests are not allowed")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope, send, status: int, detail: str) -> None:  # noqa: ANN001
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": detail}).encode()
        await send({
            "type": "http.response.start", "status": status,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                        (b"cache-control", b"no-store")],
        })
        await send({"type": "http.response.body", "body": body})
