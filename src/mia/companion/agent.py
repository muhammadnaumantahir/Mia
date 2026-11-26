"""Outbound-only Windows companion client (MIA-COMP-03)."""

from __future__ import annotations

import json
import os
import random
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mia.security.pairing import auth_statement, b64e, pairing_statement

DEFAULT_HEARTBEAT_SECONDS = 60
SESSION_RENEW_BEFORE_SECONDS = 180
MAX_RECONNECT_SECONDS = 300


class CompanionAgentError(RuntimeError):
    """Safe-to-display companion agent error; never includes credentials."""


def validate_server_url(value: str) -> str:
    """Allow HTTP only for loopback; non-loopback endpoints must use verified HTTPS."""
    raw = (value or "").strip()
    try:
        parts = urlsplit(raw)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
        ):
            raise ValueError
        host = parts.hostname.casefold().rstrip(".")
        local = host in {"localhost", "127.0.0.1", "::1"}
        if parts.scheme == "http" and not local:
            raise CompanionAgentError("Non-loopback companion servers must use HTTPS")
        if parts.path not in {"", "/"}:
            raise CompanionAgentError("Server URL must not include a path")
        # Accessing .port performs validation and rejects malformed/out-of-range values.
        _ = parts.port
    except ValueError as exc:
        raise CompanionAgentError(
            "Use a plain loopback HTTP URL or a verified HTTPS server URL"
        ) from exc
    return f"{parts.scheme}://{parts.netloc}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_expiry(value: str) -> datetime:
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise CompanionAgentError("The companion server returned an invalid session expiry") from exc
    if expiry.tzinfo is None:
        raise CompanionAgentError("The companion server returned a timezone-free session expiry")
    return expiry.astimezone(UTC)


def _write_status(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".status-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class CompanionAgent:
    """Pairs once, refreshes a scoped session, sends heartbeat and never accepts inbound work."""

    def __init__(
        self,
        identity,
        *,
        client=None,
        clock=_utcnow,
        sleep=time.sleep,
        random_value=random.random,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
    ) -> None:
        if not 10 <= heartbeat_seconds <= 300:
            raise ValueError("heartbeat_seconds must be between 10 and 300")
        self.identity = identity
        self.clock = clock
        self.sleep = sleep
        self.random_value = random_value
        self.heartbeat_seconds = heartbeat_seconds
        owns_client = client is None
        if client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - environment dependent
                raise CompanionAgentError(
                    "Install the optional companion extra: pip install -e .[companion]"
                ) from exc
            client = httpx.Client(
                timeout=10.0,
                follow_redirects=False,
                trust_env=False,
                verify=True,
            )
        self.client = client
        self._owns_client = owns_client
        self._stop = threading.Event()
        self.status_path = self.identity.home / "runtime" / "status.json"
        self.stop_path = self.identity.home / "runtime" / "stop.request"
        self.last_error: str | None = None
        self.last_success: str | None = None
        self.last_attempt: str | None = None
        self.consecutive_failures = 0
        self.state = "stopped"

    @staticmethod
    def _headers(token: str | None = None) -> dict[str, str]:
        result = {"Accept": "application/json", "User-Agent": "Mia-Windows-Companion/1.0"}
        if token:
            result["Authorization"] = f"Bearer {token}"
        return result

    def _request(
        self, method: str, path: str, *, server_url: str,
        token: str | None = None, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import httpx

        url = server_url + path
        try:
            response = self.client.request(
                method, url, json=body, headers=self._headers(token)
            )
            if 300 <= response.status_code < 400:
                raise CompanionAgentError("The companion server attempted an HTTP redirect; it was refused")
            response.raise_for_status()
            result = response.json()
        except CompanionAgentError:
            raise
        except httpx.HTTPStatusError as exc:
            # Do not echo response bodies or request headers (which can contain the bearer token).
            if exc.response.status_code in {401, 403}:
                raise CompanionAgentError(
                    "The companion session was refused or revoked; check the device in Mia and re-pair locally if needed"
                ) from exc
            if exc.response.status_code == 429:
                raise CompanionAgentError("The companion server rate-limited this device") from exc
            raise CompanionAgentError(
                f"The companion server returned HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CompanionAgentError("Could not reach the Mia companion server") from exc
        except (ValueError, TypeError) as exc:
            raise CompanionAgentError("The companion server returned an invalid response") from exc
        if not isinstance(result, dict):
            raise CompanionAgentError("The companion server returned an unexpected response")
        return result

    def _new_session(self, data: dict[str, Any], *, allow_expired_local: bool = False) -> dict[str, Any]:
        server = validate_server_url(data["server_url"])
        token = data.get("session_token") or ""
        local = urlsplit(server).hostname.casefold() in {"localhost", "127.0.0.1", "::1"}
        expiry_text = data.get("expires_at") or ""
        expiry = _parse_expiry(expiry_text) if expiry_text else None
        if (expiry is None or expiry <= self.clock()) and not local:
            raise CompanionAgentError(
                "This remote session expired while disconnected. Reconnect to Mia locally and pair again."
            )
        # Remote session renewal is bound to a still-live bearer token. Local loopback
        # renewal can re-prove the Ed25519 key after expiry without sending an expired token.
        auth_token = token if token and expiry and expiry > self.clock() else None
        challenge = self._request(
            "POST", "/api/companion/challenge", server_url=server,
            token=auth_token,
            body={"device_id": data["device_id"]},
        )
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.from_private_bytes(data["private_key"])
            signature = b64e(private_key.sign(auth_statement(
                data["device_id"], challenge["challenge_id"], challenge["nonce"]
            )))
        except Exception as exc:
            raise CompanionAgentError("The protected companion signing key is unavailable") from exc
        session = self._request(
            "POST", "/api/companion/session", server_url=server,
            token=auth_token,
            body={
                "device_id": data["device_id"],
                "challenge_id": challenge["challenge_id"],
                "signature": signature,
            },
        )
        if session.get("device_id") != data["device_id"]:
            raise CompanionAgentError("The companion server returned a different device identity")
        if not str(session.get("token") or "").startswith("mia-dev_"):
            raise CompanionAgentError("The companion server did not issue a valid device session")
        scopes = sorted({str(scope) for scope in session.get("scopes", [])})
        if scopes != sorted({str(scope) for scope in data["scopes"]}):
            raise CompanionAgentError("The companion device scopes changed; owner review is required")
        self.identity.update_session(str(session["token"]), str(session["expires_at"]))
        updated = self.identity.load()
        return updated

    def pair(self, server_url: str, code: str, device_name: str) -> dict[str, Any]:
        """Redeem an owner-created one-time code, then immediately open a signed session."""
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError as exc:
            raise CompanionAgentError(
                    "Install the optional companion extra: pip install -e .[companion]"
            ) from exc
        if self.identity.exists:
            raise CompanionAgentError(
                "This companion already has a protected identity. Revoke it in Mia, then run "
                "'mia-companion forget --confirm' before pairing a replacement."
            )
        server = validate_server_url(server_url)
        server_host = urlsplit(server).hostname.casefold()
        if server_host not in {"localhost", "127.0.0.1", "::1"}:
            raise CompanionAgentError(
                "First-time companion pairing must be performed against Mia on loopback"
            )
        name = " ".join((device_name or "").split())
        if not 1 <= len(name) <= 80:
            raise CompanionAgentError("Device name must contain 1–80 characters")
        pairing_code = (code or "").strip().upper()
        if not 1 <= len(pairing_code) <= 32:
            raise CompanionAgentError("Enter the one-time pairing code shown by Mia")
        private = Ed25519PrivateKey.generate()
        private_bytes = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        public_key = b64e(public_bytes)
        signature = b64e(private.sign(pairing_statement(pairing_code, public_key, name)))
        device = self._request(
            "POST", "/api/companion/pair", server_url=server,
            body={
                "code": pairing_code,
                "public_key": public_key,
                "device_name": name,
                "signature": signature,
            },
        )
        data = {
            "server_url": server,
            "device_id": str(device["device_id"]),
            "device_name": str(device["name"]),
            "public_key": public_key,
            "private_key": private_bytes,
            "session_token": "",
            "expires_at": "",
            "scopes": sorted({str(scope) for scope in device["scopes"]}),
            "created_at": str(device["created_at"]),
        }
        # Persist the private key before attempting session creation. If Mia restarts
        # between redemption and login, a local retry can still prove device identity.
        self.identity.save(data)
        challenge = self._request(
            "POST", "/api/companion/challenge", server_url=server,
            body={"device_id": data["device_id"]},
        )
        signature = b64e(private.sign(auth_statement(
            data["device_id"], challenge["challenge_id"], challenge["nonce"]
        )))
        session = self._request(
            "POST", "/api/companion/session", server_url=server,
            body={
                "device_id": data["device_id"],
                "challenge_id": challenge["challenge_id"],
                "signature": signature,
            },
        )
        if session.get("device_id") != data["device_id"]:
            raise CompanionAgentError("The companion server returned a different device identity")
        if not str(session.get("token") or "").startswith("mia-dev_"):
            raise CompanionAgentError("The companion server did not issue a valid device session")
        if sorted({str(scope) for scope in session.get("scopes", [])}) != data["scopes"]:
            raise CompanionAgentError("The companion device scopes changed; owner review is required")
        data["session_token"] = str(session["token"])
        data["expires_at"] = str(session["expires_at"])
        self.identity.save(data)
        self.state = "online"
        self.last_success = self.clock().isoformat()
        self.last_error = None
        self.consecutive_failures = 0
        self.publish_status()
        return self.identity.public_status()

    def poll_once(self) -> dict[str, Any]:
        data = self.identity.load()
        server = validate_server_url(data["server_url"])
        expiry_text = data.get("expires_at") or ""
        expiry = _parse_expiry(expiry_text) if expiry_text else None
        if expiry is None or expiry <= self.clock() + timedelta(seconds=SESSION_RENEW_BEFORE_SECONDS):
            data = self._new_session(data)
        result = self._request(
            "POST", "/api/companion/heartbeat", server_url=server,
            token=data["session_token"], body={},
        )
        if result.get("status") != "ok":
            raise CompanionAgentError("The companion heartbeat was not accepted")
        self.last_attempt = self.clock().isoformat()
        self.last_success = self.last_attempt
        self.last_error = None
        self.consecutive_failures = 0
        self.state = "online"
        self.publish_status()
        return {
            "status": "ok",
            "device_id": data["device_id"],
            "server_url": server,
            "expires_at": data["expires_at"],
            "scopes": list(data["scopes"]),
        }

    def public_status(self) -> dict[str, Any]:
        paired = self.identity.exists
        data = self.identity.public_status() if paired else {"paired": False}
        return {
            **data,
            "state": self.state,
            "last_attempt": self.last_attempt,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "outbound_only": True,
            "inbound_listener": False,
        }

    def publish_status(self) -> dict[str, Any]:
        status = self.public_status()
        _write_status(self.status_path, {
            **status,
            "pid": os.getpid(),
            "updated_at": self.clock().isoformat(),
        })
        return status

    def request_stop(self) -> None:
        self._stop.set()
        self.stop_path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text("stop\n", encoding="ascii")

    def run_forever(self, *, stop_event: threading.Event | None = None) -> int:
        external_stop = stop_event or threading.Event()
        self._stop.clear()
        try:
            self.stop_path.unlink()
        except FileNotFoundError:
            pass
        self.state = "starting" if self.identity.exists else "unpaired"
        self.publish_status()
        if not self.identity.exists:
            self.last_error = "Not paired. Run 'mia-companion pair' first."
            self.state = "unpaired"
            self.publish_status()
            return 2
        delay = 5.0
        try:
            while not self._stop.is_set() and not external_stop.is_set():
                if self.stop_path.exists():
                    break
                self.last_attempt = self.clock().isoformat()
                try:
                    self.poll_once()
                    delay = 5.0
                    self._wait_for_next_heartbeat(self.heartbeat_seconds, external_stop)
                except CompanionAgentError as exc:
                    self.consecutive_failures += 1
                    self.last_error = str(exc)[:240]
                    self.state = "degraded"
                    self.publish_status()
                    jitter = 0.8 + (self.random_value() * 0.4)
                    self._wait_for_next_heartbeat(
                        min(MAX_RECONNECT_SECONDS, delay) * jitter, external_stop
                    )
                    delay = min(MAX_RECONNECT_SECONDS, delay * 2)
        except KeyboardInterrupt:
            pass
        finally:
            self.state = "stopped"
            self.publish_status()
        return 0

    def _wait_for_next_heartbeat(
        self, seconds: float, external_stop: threading.Event
    ) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < deadline:
            if self._stop.is_set() or external_stop.is_set() or self.stop_path.exists():
                return
            self._stop.wait(min(1.0, deadline - time.monotonic()))

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if self._owns_client and callable(close):
            close()
