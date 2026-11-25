"""Outbound-only companion pairing, device identity and scoped sessions (MIA-COMP-02, A7).

Threat addressed: any process that can reach Mia's loopback API could call owner-only routes (for
example approving its own request). A paired companion (the future Windows agent, COMP-03) must
therefore be a *different, confined* kind of caller with an identity that can be revoked.

Protocol (the companion always initiates; Mia never connects to a device and exposes no push or
inbound control channel):

1. Owner creates a one-time pairing code (5 minutes, scopes chosen by the owner). Only its hash is stored.
2. The companion generates an Ed25519 key pair locally. The private key never leaves the device.
   It redeems the code by sending its public key and a signature over the pairing statement,
   which proves possession of the private key. The code is consumed atomically.
3. To work, the companion asks for a challenge (random nonce, 30 s, single use), signs
   ``auth|device|challenge|nonce`` and receives a short-lived session token (15 min). Mia stores
   only the token hash. A captured signature or challenge cannot be replayed.
4. Tokens carry the device's scopes only. Revoking a device ends its sessions at once, because
   every use re-checks the device record. Unknown device, wrong signature, expired or reused
   challenge all fail with the same generic error.

Not in scope here: transport encryption (loopback today; non-loopback needs TLS, LIFE-13), and
authenticating the *owner's* browser to Mia. Device tokens are confined to ``/api/companion`` by
``DeviceConfinementGuard``, so a compromised companion cannot reach owner routes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Scopes a device may ever hold. Approving, granting permissions or pairing are not here on purpose.
ALLOWED_SCOPES = frozenset({"companion.heartbeat", "companion.observe", "companion.act"})
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 10  # about 50 bits
CODE_TTL_SECONDS = 300
CHALLENGE_TTL_SECONDS = 30
SESSION_TTL_SECONDS = 900
MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 300
TOKEN_PREFIX = "mia-dev_"
GENERIC_FAILURE = "pairing or authentication failed"


class PairingError(Exception):
    """Generic, non-oracle failure for any pairing/authentication problem."""

    def __init__(self, detail: str = GENERIC_FAILURE, *, locked: bool = False) -> None:
        super().__init__(detail)
        self.locked = locked


class ScopeError(PairingError):
    """Authenticated, but the scope is not held."""


@dataclass(frozen=True)
class Caller:
    device_id: str
    scopes: frozenset[str]
    label: str


def _b64d(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise PairingError() from exc


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pairing_statement(code: str, public_key: str, device_name: str) -> bytes:
    return json.dumps(["pair", code, public_key, device_name], separators=(",", ":")).encode()


def auth_statement(device_id: str, challenge_id: str, nonce: str) -> bytes:
    return json.dumps(["auth", device_id, challenge_id, nonce], separators=(",", ":")).encode()


def _verify(public_key_b64: str, signature_b64: str, message: bytes) -> bool:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("companion pairing needs the 'cryptography' package (pip install mia[companion])") from exc
    raw = _b64d(public_key_b64)
    if len(raw) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(_b64d(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, PairingError):
        return False


def _now() -> datetime:
    return datetime.now(UTC)


class PairingService:
    def __init__(self, database_path: str | Path, *, clock: Callable[[], datetime] = _now) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS pairing_codes(
              code_hash TEXT PRIMARY KEY, label TEXT NOT NULL, scopes TEXT NOT NULL,
              expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS devices(
              device_id TEXT PRIMARY KEY, name TEXT NOT NULL, label TEXT NOT NULL, public_key TEXT NOT NULL UNIQUE,
              scopes TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, last_seen TEXT, revoked_at TEXT);
            CREATE TABLE IF NOT EXISTS challenges(
              challenge_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, nonce TEXT NOT NULL,
              expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sessions(
              token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL, expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS failures(kind TEXT NOT NULL, subject TEXT NOT NULL, at TEXT NOT NULL);
            """)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    # ---- throttling ------------------------------------------------------------------------
    def _locked(self, db: sqlite3.Connection, kind: str, subject: str) -> bool:
        since = (self.clock() - timedelta(seconds=FAILURE_WINDOW_SECONDS)).isoformat()
        count = db.execute("SELECT COUNT(*) FROM failures WHERE kind=? AND subject=? AND at>=?",
                           (kind, subject, since)).fetchone()[0]
        return count >= MAX_FAILURES

    def _fail(self, db: sqlite3.Connection, kind: str, subject: str) -> PairingError:
        db.execute("INSERT INTO failures VALUES (?,?,?)", (kind, subject, self.clock().isoformat()))
        return PairingError()

    # ---- owner side ------------------------------------------------------------------------
    def create_code(self, label: str, scopes: list[str], ttl_seconds: int = CODE_TTL_SECONDS) -> dict[str, Any]:
        label = (label or "").strip()
        if not 1 <= len(label) <= 80:
            raise ValueError("label must be 1-80 characters")
        if not scopes or not set(scopes) <= ALLOWED_SCOPES:
            raise ValueError(f"scopes must be a non-empty subset of {sorted(ALLOWED_SCOPES)}")
        if not 30 <= ttl_seconds <= CODE_TTL_SECONDS:
            raise ValueError(f"ttl_seconds must be 30-{CODE_TTL_SECONDS}")
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        expires = self.clock() + timedelta(seconds=ttl_seconds)
        with self._db() as db:
            db.execute("INSERT INTO pairing_codes VALUES (?,?,?,?,0)",
                       (_hash(code), label, json.dumps(sorted(set(scopes))), expires.isoformat()))
        return {"code": code, "expires_at": expires.isoformat(), "scopes": sorted(set(scopes)), "label": label,
                "note": "Shown once. Enter it in the companion within the time limit."}

    def list_devices(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [self._device_view(r) for r in rows]

    @staticmethod
    def _device_view(row: sqlite3.Row) -> dict[str, Any]:
        return {"device_id": row["device_id"], "name": row["name"], "label": row["label"],
                "scopes": json.loads(row["scopes"]), "status": row["status"], "created_at": row["created_at"],
                "last_seen": row["last_seen"], "revoked_at": row["revoked_at"],
                "key_fingerprint": _hash(row["public_key"])[:16]}

    def revoke(self, device_id: str) -> bool:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE devices SET status='revoked', revoked_at=? WHERE device_id=? AND status='active'",
                                 (self.clock().isoformat(), device_id)).rowcount
            db.execute("DELETE FROM sessions WHERE device_id=?", (device_id,))
            db.execute("DELETE FROM challenges WHERE device_id=?", (device_id,))
            db.execute("COMMIT")
        return bool(changed)

    def revoke_all(self) -> int:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE devices SET status='revoked', revoked_at=? WHERE status='active'",
                                 (self.clock().isoformat(),)).rowcount
            db.execute("DELETE FROM sessions")
            db.execute("DELETE FROM challenges")
            db.execute("DELETE FROM pairing_codes WHERE used=0")
            db.execute("COMMIT")
        return changed

    # ---- companion side --------------------------------------------------------------------
    def redeem(self, code: str, public_key: str, device_name: str, signature: str) -> dict[str, Any]:
        device_name = (device_name or "").strip()
        code = (code or "").strip().upper()
        with self._db() as db:
            if self._locked(db, "redeem", "global"):
                raise PairingError("too many failed attempts; try again later", locked=True)
            if not 1 <= len(device_name) <= 80 or len(code) != CODE_LENGTH:
                raise self._fail(db, "redeem", "global")
            if not _verify(public_key, signature, pairing_statement(code, public_key, device_name)):
                raise self._fail(db, "redeem", "global")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pairing_codes WHERE code_hash=?", (_hash(code),)).fetchone()
            now = self.clock()
            if row is None or row["used"] or datetime.fromisoformat(row["expires_at"]) <= now:
                db.execute("ROLLBACK")
                with self._db() as other:
                    raise self._fail(other, "redeem", "global")
            if db.execute("SELECT 1 FROM devices WHERE public_key=?", (public_key,)).fetchone():
                db.execute("ROLLBACK")
                with self._db() as other:
                    raise self._fail(other, "redeem", "global")
            db.execute("UPDATE pairing_codes SET used=1 WHERE code_hash=?", (row["code_hash"],))
            device_id = uuid.uuid4().hex
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?,?,NULL,NULL)",
                       (device_id, device_name, row["label"], public_key, row["scopes"], "active", now.isoformat()))
            db.execute("COMMIT")
            view = self._device_view(db.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone())
        return view

    def challenge(self, device_id: str) -> dict[str, Any]:
        """Always answers the same way, for known and unknown devices alike (no existence oracle)."""
        device_id = (device_id or "")[:64]
        challenge_id, nonce = uuid.uuid4().hex, b64e(secrets.token_bytes(32))
        expires = self.clock() + timedelta(seconds=CHALLENGE_TTL_SECONDS)
        with self._db() as db:
            db.execute("DELETE FROM challenges WHERE expires_at < ?", ((self.clock() - timedelta(hours=1)).isoformat(),))
            db.execute("INSERT INTO challenges VALUES (?,?,?,?,0)", (challenge_id, device_id, nonce, expires.isoformat()))
        return {"challenge_id": challenge_id, "nonce": nonce, "expires_at": expires.isoformat()}

    def open_session(self, device_id: str, challenge_id: str, signature: str) -> dict[str, Any]:
        device_id = (device_id or "")[:64]
        with self._db() as db:
            if self._locked(db, "session", device_id):
                raise PairingError("too many failed attempts; try again later", locked=True)
            db.execute("BEGIN IMMEDIATE")
            chal = db.execute("SELECT * FROM challenges WHERE challenge_id=?", (challenge_id or "",)).fetchone()
            ok = chal is not None and chal["device_id"] == device_id and not chal["used"] \
                and datetime.fromisoformat(chal["expires_at"]) > self.clock()
            if chal is not None:
                db.execute("UPDATE challenges SET used=1 WHERE challenge_id=?", (chal["challenge_id"],))  # single use, win or lose
            device = db.execute("SELECT * FROM devices WHERE device_id=? AND status='active'", (device_id,)).fetchone() if ok else None
            if device is None or not _verify(device["public_key"], signature, auth_statement(device_id, chal["challenge_id"], chal["nonce"])):
                db.execute("COMMIT")
                raise self._fail(db, "session", device_id)
            token = TOKEN_PREFIX + b64e(secrets.token_bytes(32))
            expires = self.clock() + timedelta(seconds=SESSION_TTL_SECONDS)
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (_hash(token), device_id, expires.isoformat()))
            db.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (self.clock().isoformat(), device_id))
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (self.clock().isoformat(),))
            db.execute("COMMIT")
        return {"token": token, "expires_at": expires.isoformat(), "scopes": json.loads(device["scopes"]),
                "device_id": device_id}

    def verify_token(self, token: str, *, scope: str | None = None, touch: bool = False) -> Caller:
        if not isinstance(token, str) or not token.startswith(TOKEN_PREFIX):
            raise PairingError()
        with self._db() as db:
            row = db.execute(
                "SELECT s.expires_at, d.device_id, d.scopes, d.label, d.status FROM sessions s "
                "JOIN devices d ON d.device_id=s.device_id WHERE s.token_hash=?", (_hash(token),)).fetchone()
            if row is None or row["status"] != "active" or datetime.fromisoformat(row["expires_at"]) <= self.clock():
                raise PairingError()
            caller = Caller(row["device_id"], frozenset(json.loads(row["scopes"])), row["label"])
            if scope is not None and scope not in caller.scopes:
                raise ScopeError(f"this device does not hold {scope}")
            if touch:
                db.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (self.clock().isoformat(), row["device_id"]))
        return caller


class DeviceConfinementGuard:
    """ASGI middleware: a request bearing a device token may only use /api/companion/*.

    A compromised companion therefore cannot call owner routes (approvals, grants, files, pairing).
    Requests without a device token pass through unchanged; this does not authenticate the owner.
    """

    ALLOWED_PREFIX = "/api/companion/"

    def __init__(self, app) -> None:  # noqa: ANN001 - ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] in {"http", "websocket"}:
            for key, value in scope.get("headers", []):
                if key == b"authorization" and value.decode("latin-1").strip().lower().split(" ", 1)[-1].startswith(TOKEN_PREFIX.lower()):
                    if not scope["path"].startswith(self.ALLOWED_PREFIX):
                        if scope["type"] == "websocket":
                            await send({"type": "websocket.close", "code": 1008})
                        else:
                            body = b'{"detail":"Device credentials cannot be used here"}'
                            await send({"type": "http.response.start", "status": 403,
                                        "headers": [(b"content-type", b"application/json"),
                                                    (b"content-length", str(len(body)).encode())]})
                            await send({"type": "http.response.body", "body": body})
                        return
        await self.app(scope, receive, send)
