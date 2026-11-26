"""Current-user protected companion device identity and session state."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Protocol


class SecretProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...
    def unprotect(self, value: bytes) -> bytes: ...


def default_companion_home() -> Path:
    override = os.getenv("MIA_COMPANION_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
    if not base:
        raise RuntimeError("Windows user profile storage is unavailable")
    return Path(base) / "Mia" / "Companion"


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class CompanionIdentity:
    """Stores public metadata in JSON and encrypts private key/token with Windows DPAPI."""

    FORMAT_VERSION = 1

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        protector: SecretProtector | None = None,
    ) -> None:
        self.home = Path(home) if home is not None else default_companion_home()
        if protector is None:
            from mia.companion.dpapi import WindowsDPAPI

            protector = WindowsDPAPI()
        self.protector = protector
        self.path = self.home / "device.json"
        self._data: dict[str, Any] | None = None

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> dict[str, Any]:
        if self._data is not None:
            return dict(self._data)
        if not self.exists:
            raise FileNotFoundError("This companion is not paired. Run 'mia-companion pair' first.")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("format_version") != self.FORMAT_VERSION:
                raise ValueError("unsupported companion identity format")
            private_key = self.protector.unprotect(_decode(payload["private_key_dpapi"]))
            token = self.protector.unprotect(_decode(payload["session_token_dpapi"]))
            if token == b"pending":
                token = b""
            if len(private_key) != 32 or (token and not token.startswith(b"mia-dev_")):
                raise ValueError("protected companion identity is invalid")
            data = {
                "server_url": payload["server_url"],
                "device_id": payload["device_id"],
                "device_name": payload["device_name"],
                "public_key": payload["public_key"],
                "private_key": private_key,
                "session_token": token.decode("ascii"),
                "expires_at": payload["expires_at"],
                "scopes": list(payload.get("scopes") or []),
                "created_at": payload["created_at"],
            }
        except Exception as exc:
            raise RuntimeError(
                "Could not open the companion identity for this Windows user; secrets were not recovered"
            ) from exc
        self._data = data
        return dict(data)

    def save(self, data: dict[str, Any]) -> None:
        required = {
            "server_url", "device_id", "device_name", "public_key", "private_key",
            "session_token", "expires_at", "scopes", "created_at",
        }
        if not required.issubset(data):
            raise ValueError("companion identity is incomplete")
        private_key = bytes(data["private_key"])
        session_token = str(data["session_token"]).encode("ascii") or b"pending"
        if len(private_key) != 32 or not (
            session_token == b"pending" or session_token.startswith(b"mia-dev_")
        ):
            raise ValueError("refusing to persist an invalid companion identity")
        self.home.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": self.FORMAT_VERSION,
            "server_url": str(data["server_url"]),
            "device_id": str(data["device_id"]),
            "device_name": str(data["device_name"]),
            "public_key": str(data["public_key"]),
            "private_key_dpapi": _encode(self.protector.protect(private_key)),
            "session_token_dpapi": _encode(self.protector.protect(session_token)),
            "expires_at": str(data["expires_at"]),
            "scopes": sorted({str(item) for item in data["scopes"]}),
            "created_at": str(data["created_at"]),
        }
        encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".device-", suffix=".tmp", dir=self.home)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        self._data = {
            **data,
            "private_key": private_key,
            "session_token": "" if session_token == b"pending" else session_token.decode("ascii"),
        }

    def update_session(self, token: str, expires_at: str) -> dict[str, Any]:
        data = self.load()
        data["session_token"] = token
        data["expires_at"] = expires_at
        self.save(data)
        return self.public_status()

    def clear(self) -> bool:
        self._data = None
        try:
            self.path.unlink()
            return True
        except FileNotFoundError:
            return False

    def public_status(self) -> dict[str, Any]:
        data = self.load()
        return {
            "paired": True,
            "server_url": data["server_url"],
            "device_id": data["device_id"],
            "device_name": data["device_name"],
            "public_key": data["public_key"],
            "expires_at": data["expires_at"],
            "scopes": list(data["scopes"]),
            "created_at": data["created_at"],
            "secrets_protected": True,
        }
