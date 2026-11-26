"""WebAuthn-backed owner sessions for high-risk owner actions (MIA-COMP-04-OWNER).

The first owner passkey is enrolled only after a short-lived code has been created from the
local Mia host CLI. All registration and authentication ceremonies require user verification.
The server stores credential public keys and hashed session/bootstrap tokens; private passkey
material never reaches Mia.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from http.cookies import SimpleCookie
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from mia.security.capabilities import ApprovalClass, classify

SESSION_TTL_SECONDS = 8 * 60 * 60
CHALLENGE_TTL_SECONDS = 5 * 60
BOOTSTRAP_TTL_SECONDS = 5 * 60
COOKIE_NAME = "mia_owner"
RP_ID = "localhost"
RP_NAME = "Mia Personal AI"
USER_ID = b"mia-owner-account-v1"


class OwnerAuthError(RuntimeError):
    """Owner authentication failed or the ceremony is invalid."""


class OwnerAuthNotConfigured(OwnerAuthError):
    """No initial owner passkey has been enrolled."""


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64(value: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _origin(value: str | None, host: str | None, scheme: str) -> str:
    if not value or not host:
        raise OwnerAuthError("Open Mia at http://localhost:<port>; an exact browser Origin is required")
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname != RP_ID
        or parts.netloc.casefold() != host.casefold()
        or parts.path or parts.query or parts.fragment
        or parts.username is not None or parts.password is not None
        or parts.scheme != scheme
    ):
        raise OwnerAuthError("Owner passkeys are bound to the exact localhost browser origin")
    return value


def _webauthn():
    try:
        from webauthn import (
            base64url_to_bytes,
            generate_authentication_options,
            generate_registration_options,
            options_to_json,
            verify_authentication_response,
            verify_registration_response,
        )
        from webauthn.helpers.structs import (
            AuthenticatorSelectionCriteria,
            PublicKeyCredentialDescriptor,
            ResidentKeyRequirement,
            UserVerificationRequirement,
        )
    except ImportError as exc:  # pragma: no cover - depends on the optional install extra
        raise OwnerAuthError(
            'Install the optional owner-auth extra: pip install -e ".[owner-auth]"'
        ) from exc
    return {
        "base64url_to_bytes": base64url_to_bytes,
        "generate_authentication_options": generate_authentication_options,
        "generate_registration_options": generate_registration_options,
        "options_to_json": options_to_json,
        "verify_authentication_response": verify_authentication_response,
        "verify_registration_response": verify_registration_response,
        "AuthenticatorSelectionCriteria": AuthenticatorSelectionCriteria,
        "PublicKeyCredentialDescriptor": PublicKeyCredentialDescriptor,
        "ResidentKeyRequirement": ResidentKeyRequirement,
        "UserVerificationRequirement": UserVerificationRequirement,
    }


class OwnerAuthService:
    """Single-owner passkey enrolment, authentication, hashed sessions and recovery."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        clock: Callable[[], datetime] = _now,
        webauthn_loader: Callable[[], dict[str, Any]] = _webauthn,
    ) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.webauthn_loader = webauthn_loader
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS owner_credentials(
              credential_id TEXT PRIMARY KEY,
              public_key BLOB NOT NULL,
              sign_count INTEGER NOT NULL DEFAULT 0,
              transports_json TEXT NOT NULL DEFAULT '[]',
              device_type TEXT NOT NULL DEFAULT 'unknown',
              backed_up INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              last_used_at TEXT
            );
            CREATE TABLE IF NOT EXISTS owner_bootstrap_codes(
              code_hash TEXT PRIMARY KEY,
              expires_at TEXT NOT NULL,
              used INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_challenges(
              challenge_id TEXT PRIMARY KEY,
              challenge BLOB NOT NULL,
              purpose TEXT NOT NULL,
              expected_origin TEXT NOT NULL,
              bootstrap_hash TEXT,
              session_hash TEXT,
              expires_at TEXT NOT NULL,
              used INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owner_sessions(
              token_hash TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL,
              last_used_at TEXT NOT NULL,
              revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_owner_challenges_expiry
              ON owner_challenges(purpose, expires_at, used);
            CREATE INDEX IF NOT EXISTS idx_owner_sessions_expiry
              ON owner_sessions(expires_at, revoked_at);
            """)

    def credential_count(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM owner_credentials").fetchone()[0])

    @property
    def is_configured(self) -> bool:
        return self.credential_count() > 0

    def create_bootstrap_code(self) -> dict[str, Any]:
        """Create a one-time bootstrap secret. Call only from the local host CLI."""
        if self.is_configured:
            raise OwnerAuthError(
                "An owner passkey is already registered; authenticate to add another passkey"
            )
        code = secrets.token_urlsafe(32)
        now = self.clock()
        expires = now + timedelta(seconds=BOOTSTRAP_TTL_SECONDS)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM owner_credentials LIMIT 1").fetchone():
                db.execute("ROLLBACK")
                raise OwnerAuthError("An owner passkey is already registered")
            db.execute("DELETE FROM owner_bootstrap_codes WHERE used=1 OR expires_at<=?",
                       (now.isoformat(),))
            db.execute("DELETE FROM owner_bootstrap_codes")
            db.execute("INSERT INTO owner_bootstrap_codes VALUES (?,?,0,?)",
                       (_hash(code), expires.isoformat(), now.isoformat()))
            db.execute("COMMIT")
        return {
            "code": code,
            "expires_at": expires.isoformat(),
            "expires_in_seconds": BOOTSTRAP_TTL_SECONDS,
            "note": "Shown once. Enter it in the local /owner-auth page to enrol the first passkey.",
        }

    def has_valid_session(self, token: str | None) -> bool:
        if not token or len(token) > 256:
            return False
        now = self.clock().isoformat()
        with self._db() as db:
            row = db.execute(
                "SELECT token_hash FROM owner_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
                (_hash(token), now),
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "UPDATE owner_sessions SET last_used_at=? WHERE token_hash=?",
                (now, row["token_hash"]),
            )
        return True

    def _bootstrap_hash(self, code: str | None) -> str:
        if not code:
            raise OwnerAuthError("Enter the one-time bootstrap code created on the Mia host")
        digest = _hash(code.strip())
        with self._db() as db:
            row = db.execute(
                "SELECT expires_at, used FROM owner_bootstrap_codes WHERE code_hash=?",
                (digest,),
            ).fetchone()
        if row is None or row["used"] or datetime.fromisoformat(row["expires_at"]) <= self.clock():
            raise OwnerAuthError("The owner bootstrap code is invalid, expired or already used")
        return digest

    def _new_challenge(
        self, *, purpose: str, challenge: bytes, origin: str,
        bootstrap_hash: str | None = None, session_hash: str | None = None,
    ) -> str:
        challenge_id = secrets.token_urlsafe(24)
        now = self.clock()
        expires = now + timedelta(seconds=CHALLENGE_TTL_SECONDS)
        with self._db() as db:
            db.execute(
                "INSERT INTO owner_challenges VALUES (?,?,?,?,?,?,?,0,?)",
                (challenge_id, challenge, purpose, origin, bootstrap_hash, session_hash,
                 expires.isoformat(), now.isoformat()),
            )
            db.execute("DELETE FROM owner_challenges WHERE expires_at<=?", (now.isoformat(),))
        return challenge_id

    def begin_registration(
        self, *, origin: str, bootstrap_code: str | None = None,
        session_token: str | None = None,
    ) -> dict[str, Any]:
        if self.is_configured:
            if not self.has_valid_session(session_token):
                raise OwnerAuthError("Authenticate with the existing owner passkey before adding another")
            session_hash = _hash(session_token or "")
            bootstrap_hash = None
        else:
            bootstrap_hash = self._bootstrap_hash(bootstrap_code)
            session_hash = None

        api = self.webauthn_loader()
        existing = []
        with self._db() as db:
            existing = [
                api["PublicKeyCredentialDescriptor"](
                    id=api["base64url_to_bytes"](row["credential_id"])
                )
                for row in db.execute("SELECT credential_id FROM owner_credentials")
            ]
        options = api["generate_registration_options"](
            rp_id=RP_ID,
            rp_name=RP_NAME,
            user_id=USER_ID,
            user_name="Mia Owner",
            user_display_name="Mia Owner",
            timeout=60_000,
            exclude_credentials=existing,
            authenticator_selection=api["AuthenticatorSelectionCriteria"](
                resident_key=api["ResidentKeyRequirement"].PREFERRED,
                user_verification=api["UserVerificationRequirement"].REQUIRED,
            ),
        )
        challenge_id = self._new_challenge(
            purpose="registration",
            challenge=options.challenge,
            origin=origin,
            bootstrap_hash=bootstrap_hash,
            session_hash=session_hash,
        )
        return {"challenge_id": challenge_id, "publicKey": json.loads(api["options_to_json"](options))}

    def _take_challenge(self, challenge_id: str, *, purpose: str, origin: str) -> sqlite3.Row:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM owner_challenges WHERE challenge_id=?",
                (challenge_id,),
            ).fetchone()
            if (
                row is None or row["purpose"] != purpose or row["used"]
                or row["expected_origin"] != origin
                or datetime.fromisoformat(row["expires_at"]) <= self.clock()
            ):
                db.execute("ROLLBACK")
                raise OwnerAuthError("The owner-auth challenge is missing, expired, mismatched or already used")
            db.execute("UPDATE owner_challenges SET used=1 WHERE challenge_id=? AND used=0",
                       (challenge_id,))
            db.execute("COMMIT")
        return row

    def finish_registration(
        self, *, challenge_id: str, credential: dict[str, Any], origin: str,
        bootstrap_code: str | None = None, session_token: str | None = None,
    ) -> str:
        challenge = self._take_challenge(challenge_id, purpose="registration", origin=origin)
        bootstrap_hash = challenge["bootstrap_hash"]
        if bootstrap_hash:
            if not hmac.compare_digest(_hash((bootstrap_code or "").strip()), bootstrap_hash):
                raise OwnerAuthError("The bootstrap code does not match this registration")
            if self.is_configured:
                raise OwnerAuthError("The first owner passkey has already been registered")
            with self._db() as db:
                code = db.execute(
                    "SELECT expires_at, used FROM owner_bootstrap_codes WHERE code_hash=?",
                    (bootstrap_hash,),
                ).fetchone()
            if code is None or code["used"] or datetime.fromisoformat(code["expires_at"]) <= self.clock():
                raise OwnerAuthError("The owner bootstrap code is invalid or expired")
        else:
            if not session_token or not hmac.compare_digest(
                _hash(session_token), challenge["session_hash"] or ""
            ) or not self.has_valid_session(session_token):
                raise OwnerAuthError("The current owner session is required to add another passkey")

        api = self.webauthn_loader()
        try:
            verified = api["verify_registration_response"](
                credential=credential,
                expected_challenge=bytes(challenge["challenge"]),
                expected_rp_id=RP_ID,
                expected_origin=origin,
                require_user_verification=True,
            )
        except Exception as exc:
            raise OwnerAuthError("The passkey registration response could not be verified") from exc

        credential_id = _b64(verified.credential_id)
        transports = credential.get("response", {}).get("transports", [])
        if not isinstance(transports, list):
            transports = []
        transports = [str(item)[:20] for item in transports[:8]]
        now = self.clock().isoformat()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if bootstrap_hash:
                if db.execute("SELECT 1 FROM owner_credentials LIMIT 1").fetchone():
                    db.execute("ROLLBACK")
                    raise OwnerAuthError("The first owner passkey was registered by another ceremony")
                updated = db.execute(
                    "UPDATE owner_bootstrap_codes SET used=1 WHERE code_hash=? AND used=0 AND expires_at>?",
                    (bootstrap_hash, now),
                )
                if updated.rowcount != 1:
                    db.execute("ROLLBACK")
                    raise OwnerAuthError("The owner bootstrap code has already been used")
            else:
                if not db.execute(
                    "SELECT 1 FROM owner_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
                    (challenge["session_hash"], now),
                ).fetchone():
                    db.execute("ROLLBACK")
                    raise OwnerAuthError("The owner session expired during passkey registration")
            db.execute(
                "INSERT INTO owner_credentials "
                "(credential_id,public_key,sign_count,transports_json,device_type,backed_up,created_at,last_used_at) "
                "VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    credential_id, verified.credential_public_key, int(verified.sign_count),
                    json.dumps(transports), str(getattr(verified, "credential_device_type", "unknown")),
                    int(bool(getattr(verified, "credential_backed_up", False))), now,
                ),
            )
            db.execute("COMMIT")
        return self._create_session()

    def begin_authentication(self, *, origin: str) -> dict[str, Any]:
        api = self.webauthn_loader()
        with self._db() as db:
            rows = db.execute(
                "SELECT credential_id FROM owner_credentials ORDER BY created_at"
            ).fetchall()
        if not rows:
            raise OwnerAuthNotConfigured("No owner passkey has been registered")
        allow = [
            api["PublicKeyCredentialDescriptor"](id=api["base64url_to_bytes"](row["credential_id"]))
            for row in rows
        ]
        options = api["generate_authentication_options"](
            rp_id=RP_ID,
            allow_credentials=allow,
            timeout=60_000,
            user_verification=api["UserVerificationRequirement"].REQUIRED,
        )
        challenge_id = self._new_challenge(
            purpose="authentication", challenge=options.challenge, origin=origin
        )
        return {"challenge_id": challenge_id, "publicKey": json.loads(api["options_to_json"](options))}

    def finish_authentication(
        self, *, challenge_id: str, credential: dict[str, Any], origin: str
    ) -> str:
        challenge = self._take_challenge(challenge_id, purpose="authentication", origin=origin)
        credential_id = credential.get("id")
        if not isinstance(credential_id, str) or not credential_id:
            raise OwnerAuthError("The authenticator did not identify its credential")
        with self._db() as db:
            stored = db.execute(
                "SELECT * FROM owner_credentials WHERE credential_id=?",
                (credential_id,),
            ).fetchone()
        if stored is None:
            raise OwnerAuthError("This passkey is not registered with Mia")
        api = self.webauthn_loader()
        try:
            verified = api["verify_authentication_response"](
                credential=credential,
                expected_challenge=bytes(challenge["challenge"]),
                expected_rp_id=RP_ID,
                expected_origin=origin,
                credential_public_key=bytes(stored["public_key"]),
                credential_current_sign_count=int(stored["sign_count"]),
                require_user_verification=True,
            )
        except Exception as exc:
            raise OwnerAuthError("The passkey assertion could not be verified") from exc
        new_count = int(verified.new_sign_count)
        old_count = int(stored["sign_count"])
        # Synced multi-device passkeys may report a zero counter. Preserve a prior
        # positive counter, but reject a non-zero replay/non-increasing counter.
        if new_count and old_count and new_count <= old_count:
            raise OwnerAuthError("The authenticator signature counter did not advance")
        now = self.clock().isoformat()
        with self._db() as db:
            changed = db.execute(
                "UPDATE owner_credentials SET sign_count=?, last_used_at=? WHERE credential_id=?",
                (max(old_count, new_count), now, credential_id),
            )
            if changed.rowcount != 1:
                raise OwnerAuthError("The owner credential changed during authentication")
        return self._create_session()

    def _create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        expires = now + timedelta(seconds=SESSION_TTL_SECONDS)
        with self._db() as db:
            db.execute(
                "INSERT INTO owner_sessions VALUES (?,?,?,?,NULL)",
                (_hash(token), now.isoformat(), expires.isoformat(), now.isoformat()),
            )
        return token

    def revoke_session(self, token: str | None) -> bool:
        if not token:
            return False
        with self._db() as db:
            changed = db.execute(
                "UPDATE owner_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                (self.clock().isoformat(), _hash(token)),
            )
        return changed.rowcount == 1

    def status(self, token: str | None) -> dict[str, Any]:
        count = self.credential_count()
        return {
            "configured": count > 0,
            "authenticated": self.has_valid_session(token),
            "credential_count": count,
            "user_verification_required": True,
            "session_ttl_seconds": SESSION_TTL_SECONDS,
            "origin_requirement": "http://localhost:<port> or https://localhost:<port>",
        }

class OwnerAuthGuard:
    """Require a verified owner passkey session for high-risk API mutations.

    Safety reductions such as stop-all, revoke-all and rejection remain available even
    without a session. High-risk confirmation, grant-gated and execute routes fail closed
    until an owner passkey has been enrolled and the browser has a live HttpOnly session.
    """

    def __init__(self, app, service: OwnerAuthService) -> None:  # noqa: ANN001
        self.app = app
        self.service = service

    @staticmethod
    def requires_owner(method: str, path: str) -> bool:
        if method.upper() in {"GET", "HEAD", "OPTIONS"}:
            return False
        capability = classify(method, path)
        if capability is None:
            return False
        return capability.approval in {
            ApprovalClass.OWNER_CONFIRM,
            ApprovalClass.PREVIEW_THEN_EXECUTE,
            ApprovalClass.GRANT_GATED,
        }

    @staticmethod
    def _cookie(headers: dict[str, str]) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get("cookie", ""))
            morsel = cookie.get(COOKIE_NAME)
            return morsel.value if morsel else None
        except Exception:
            return None

    @staticmethod
    def _headers(scope) -> dict[str, str]:  # noqa: ANN001
        return {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http" or not self.requires_owner(
            scope.get("method", "GET"), scope.get("path", "")
        ):
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        host = headers.get("host", "")
        origin = headers.get("origin", "")
        scheme = scope.get("scheme", "http")
        try:
            # A passkey credential is explicitly registered for RP ID localhost. Even
            # when LoopbackGuard is disabled in a test harness, an owner session cannot
            # be used from an IP, foreign host, missing Origin or different browser origin.
            _origin(origin, host, scheme)
        except OwnerAuthError as exc:
            response = JSONResponse(
                {"detail": str(exc), "code": "owner_auth_origin_required",
                 "login_url": "/owner-auth"},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        if not self.service.is_configured:
            response = JSONResponse(
                {
                    "detail": "Owner passkey setup is required before this high-risk action.",
                    "code": "owner_auth_setup_required",
                    "login_url": "/owner-auth",
                    "bootstrap_command": "mia-owner-auth bootstrap",
                },
                status_code=428,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        token = self._cookie(headers)
        if not self.service.has_valid_session(token):
            response = JSONResponse(
                {
                    "detail": "Authenticate with the owner passkey before this high-risk action.",
                    "code": "owner_auth_required",
                    "login_url": "/owner-auth",
                },
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

