"""MIA-COMP-04-OWNER: WebAuthn bootstrap, session and final-boundary owner guard."""

import base64
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mia.brain.broker import OwnerPermissionEvaluator

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.security.owner_auth import (
    OwnerAuthError,
    OwnerAuthGuard,
    OwnerAuthNotConfigured,
    OwnerAuthService,
    CURRENT_OWNER_SESSION,
    COOKIE_NAME,
    SESSION_TTL_SECONDS,
    _hash,
    _origin,
)
from mia.memory.store import MemoryStore


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeWebAuthn:
    class Enum:
        REQUIRED = "required"
        PREFERRED = "preferred"

    class Descriptor:
        def __init__(self, id):
            self.id = id

    class Selection:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self):
        self.registration_verified = None
        self.authentication_verified = None
        self.registration_call = None
        self.authentication_call = None
        self.challenge = b"owner-passkey-challenge"

    @staticmethod
    def b64decode(value):
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def b64encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    def generate_registration_options(self, **kwargs):
        self.registration_call = kwargs
        return SimpleNamespace(challenge=self.challenge)

    def generate_authentication_options(self, **kwargs):
        self.authentication_call = kwargs
        self.challenge = b"owner-authentication-challenge"
        return SimpleNamespace(challenge=self.challenge)

    @staticmethod
    def options_to_json(options):
        return '{"challenge":"' + FakeWebAuthn.b64encode(options.challenge) + '"}'

    def verify_registration_response(self, **kwargs):
        self.registration_verified = kwargs
        return SimpleNamespace(
            credential_id=b"credential-id-1",
            credential_public_key=b"public-key-bytes",
            sign_count=0,
            credential_device_type="multi_device",
            credential_backed_up=True,
        )

    def verify_authentication_response(self, **kwargs):
        self.authentication_verified = kwargs
        return SimpleNamespace(new_sign_count=1)


def api_loader(fake):
    return {
        "base64url_to_bytes": fake.b64decode,
        "generate_registration_options": fake.generate_registration_options,
        "generate_authentication_options": fake.generate_authentication_options,
        "options_to_json": fake.options_to_json,
        "verify_registration_response": fake.verify_registration_response,
        "verify_authentication_response": fake.verify_authentication_response,
        "AuthenticatorSelectionCriteria": fake.Selection,
        "PublicKeyCredentialDescriptor": fake.Descriptor,
        "ResidentKeyRequirement": fake.Enum,
        "UserVerificationRequirement": fake.Enum,
    }


@pytest.fixture
def owner_service(tmp_path):
    fake = FakeWebAuthn()
    clock = Clock()
    service = OwnerAuthService(
        tmp_path / "owner-auth.sqlite3",
        clock=clock,
        webauthn_loader=lambda: api_loader(fake),
    )
    return service, fake, clock


def test_origin_is_exact_localhost_and_allows_default_port_normalization():
    assert _origin("http://localhost:8765", "localhost:8765", "http") == "http://localhost:8765"
    assert _origin("http://localhost", "localhost:80", "http") == "http://localhost"
    for origin, host, scheme in (
        ("http://127.0.0.1:8765", "127.0.0.1:8765", "http"),
        ("https://evil.example", "evil.example", "https"),
        ("http://localhost:8765", "localhost:8766", "http"),
        ("null", "localhost:8765", "http"),
    ):
        with pytest.raises(OwnerAuthError):
            _origin(origin, host, scheme)


def test_bootstrap_code_is_short_lived_hashed_and_single_use(owner_service):
    service, _fake, clock = owner_service
    issued = service.create_bootstrap_code()
    assert len(issued["code"]) > 30
    with service._db() as db:
        stored = db.execute("SELECT code_hash,expires_at,used FROM owner_bootstrap_codes").fetchone()
    assert issued["code"] not in stored["code_hash"]
    assert stored["used"] == 0
    assert service.create_bootstrap_code()["code"] != issued["code"]  # replacement invalidates the old code
    with pytest.raises(OwnerAuthError, match="invalid, expired"):
        service.begin_registration(
            origin="http://localhost:8765", bootstrap_code=issued["code"]
        )
    issued = service.create_bootstrap_code()
    clock.advance(seconds=301)
    with pytest.raises(OwnerAuthError, match="invalid, expired"):
        service.begin_registration(
            origin="http://localhost:8765", bootstrap_code=issued["code"]
        )


def test_first_passkey_registration_and_login_require_user_verification(owner_service):
    service, fake, _clock = owner_service
    bootstrap = service.create_bootstrap_code()
    registration = service.begin_registration(
        origin="http://localhost:8765", bootstrap_code=bootstrap["code"]
    )
    assert registration["challenge_id"]
    assert fake.registration_call["rp_id"] == "localhost"
    assert fake.registration_call["authenticator_selection"].kwargs["user_verification"] == "required"

    registration_session = service.finish_registration(
        challenge_id=registration["challenge_id"],
        credential={
            "id": FakeWebAuthn.b64encode(b"credential-id-1"),
            "response": {"transports": ["internal"]},
        },
        origin="http://localhost:8765",
        bootstrap_code=bootstrap["code"],
    )
    assert service.is_configured
    assert service.has_valid_session(registration_session)
    assert fake.registration_verified["require_user_verification"] is True
    with pytest.raises(OwnerAuthError, match="already been registered"):
        service.create_bootstrap_code()

    auth = service.begin_authentication(origin="http://localhost:8765")
    assert fake.authentication_call["rp_id"] == "localhost"
    assert fake.authentication_call["user_verification"] == "required"
    session = service.finish_authentication(
        challenge_id=auth["challenge_id"],
        credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
        origin="http://localhost:8765",
    )
    assert service.has_valid_session(session)
    assert fake.authentication_verified["require_user_verification"] is True


def test_non_increasing_passkey_sign_counter_is_rejected(owner_service):
    service, fake, _clock = owner_service
    bootstrap = service.create_bootstrap_code()
    options = service.begin_registration(
        origin="http://localhost:8765", bootstrap_code=bootstrap["code"]
    )
    service.finish_registration(
        challenge_id=options["challenge_id"],
        credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
        origin="http://localhost:8765",
        bootstrap_code=bootstrap["code"],
    )

    first = service.begin_authentication(origin="http://localhost:8765")
    session = service.finish_authentication(
        challenge_id=first["challenge_id"],
        credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
        origin="http://localhost:8765",
    )
    assert service.has_valid_session(session)

    replayed_counter = service.begin_authentication(origin="http://localhost:8765")
    with pytest.raises(OwnerAuthError, match="counter did not advance"):
        service.finish_authentication(
            challenge_id=replayed_counter["challenge_id"],
            credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
            origin="http://localhost:8765",
        )


def test_registration_challenge_is_single_use_and_bound_to_exact_origin(owner_service):
    service, _fake, _clock = owner_service
    code = service.create_bootstrap_code()["code"]
    options = service.begin_registration(origin="http://localhost:8765", bootstrap_code=code)
    with pytest.raises(OwnerAuthError, match="mismatched"):
        service.finish_registration(
            challenge_id=options["challenge_id"],
            credential={"id": "credential-id-1", "response": {}},
            origin="http://localhost:8766",
            bootstrap_code=code,
        )
    # Origin mismatch does not consume the challenge; an exact-origin attempt can proceed.
    service.finish_registration(
        challenge_id=options["challenge_id"],
        credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
        origin="http://localhost:8765",
        bootstrap_code=code,
    )
    with pytest.raises(OwnerAuthError, match="already used"):
        service.finish_registration(
            challenge_id=options["challenge_id"],
            credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
            origin="http://localhost:8765",
            bootstrap_code=code,
        )


def test_owner_session_is_hashed_expires_and_can_be_revoked(owner_service):
    service, _fake, clock = owner_service
    bootstrap = service.create_bootstrap_code()
    options = service.begin_registration(
        origin="http://localhost:8765", bootstrap_code=bootstrap["code"]
    )
    token = service.finish_registration(
        challenge_id=options["challenge_id"],
        credential={"id": FakeWebAuthn.b64encode(b"credential-id-1"), "response": {}},
        origin="http://localhost:8765",
        bootstrap_code=bootstrap["code"],
    )
    with service._db() as db:
        stored = db.execute("SELECT token_hash,expires_at FROM owner_sessions").fetchone()
    assert token not in stored["token_hash"]
    assert service.has_valid_session(token)
    assert service.revoke_session(token) is True
    assert service.has_valid_session(token) is False
    clock.advance(seconds=SESSION_TTL_SECONDS + 1)
    assert service.has_valid_session(token) is False


def test_brain_write_mode_tools_require_owner_session_even_with_existing_grants():
    class GrantedPolicy:
        def authorize(self, _spec, path=None):
            return SimpleNamespace(allowed=True, requires_approval=False, reason="grant active")

    class Registry:
        def get(self, _name):
            return object()

    tool = SimpleNamespace(mode="write", name="workflow.run_internal",
                           permission="workflow.run_internal")
    policy, registry = GrantedPolicy(), Registry()
    denied = OwnerPermissionEvaluator(
        permission_policy=policy, capability_registry=registry,
        owner_session=lambda: False,
    ).evaluate(tool, {})
    assert denied.allowed is False
    allowed = OwnerPermissionEvaluator(
        permission_policy=policy, capability_registry=registry,
        owner_session=lambda: True,
    ).evaluate(tool, {})
    assert allowed.allowed is True


def test_owner_session_context_is_scoped_to_authenticated_asgi_request(owner_service):
    service, _fake, _clock = owner_service
    token = service._create_session()
    observed = []

    async def inner(scope, receive, send):
        observed.append(CURRENT_OWNER_SESSION.get())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = OwnerAuthGuard(inner, service)
    scope = {
        "type": "http", "method": "GET", "path": "/api/computer/approvals/cards",
        "scheme": "http",
        "headers": [
            (b"host", b"localhost:8765"),
            (b"cookie", f"{COOKIE_NAME}={token}".encode()),
        ],
    }
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(_message):
        return None

    asyncio.run(guard(scope, receive, send))
    assert observed == [True]
    assert CURRENT_OWNER_SESSION.get() is False


def test_guard_requires_owner_auth_but_preserves_safety_controls():
    assert OwnerAuthGuard.requires_owner("POST", "/api/computer/approvals/abc/approve")
    assert OwnerAuthGuard.requires_owner("POST", "/api/approvals/abc/execute")
    assert OwnerAuthGuard.requires_owner("POST", "/api/computer/permissions")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/computer/stop-all")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/computer/permissions/revoke-all")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/approvals/abc/reject")
    assert OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/run")
    assert OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/retry-step")
    assert OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/reconcile-step")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/pause")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/cancel")
    assert not OwnerAuthGuard.requires_owner("POST", "/api/workflows/abc/reject-step")
    assert OwnerAuthGuard.requires_owner("GET", "/api/computer/approvals")
    assert OwnerAuthGuard.requires_owner("GET", "/api/computer/approvals/cards")
    assert OwnerAuthGuard.requires_owner("GET", "/api/companion-admin/devices")
    assert not OwnerAuthGuard.requires_owner("GET", "/api/owner-auth/status")


def test_live_app_fails_closed_until_owner_passkey_is_enrolled(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            memory=MemoryStore(tmp_path / "memory.sqlite3"),
            local_guard=True,
        ),
        base_url="http://localhost:8765",
    )
    response = client.post(
        "/api/computer/approvals/not-real/approve",
        headers={"Origin": "http://localhost:8765"},
    )
    assert response.status_code == 428
    assert response.json()["code"] == "owner_auth_setup_required"
    assert response.json()["bootstrap_command"] == "mia-owner-auth bootstrap"

    workflow_run = client.post(
        "/api/workflows/not-real/run",
        headers={"Origin": "http://localhost:8765"},
    )
    assert workflow_run.status_code == 428
    assert workflow_run.json()["code"] == "owner_auth_setup_required"

    workflow_retry = client.post(
        "/api/workflows/not-real/retry-step",
        json={"step": 1},
        headers={"Origin": "http://localhost:8765"},
    )
    assert workflow_retry.status_code == 428

    workflow_reconcile = client.post(
        "/api/workflows/not-real/reconcile-step",
        json={"step": 1, "decision": "not_applied"},
        headers={"Origin": "http://localhost:8765"},
    )
    assert workflow_reconcile.status_code == 428

    safety = client.post(
        "/api/computer/stop-all",
        headers={"Origin": "http://localhost:8765"},
    )
    assert safety.status_code != 428

    approval_cards = client.get("/api/computer/approvals/cards")
    assert approval_cards.status_code == 428
    assert approval_cards.json()["code"] == "owner_auth_setup_required"

    device_admin = client.get("/api/companion-admin/devices")
    assert device_admin.status_code == 428
    assert device_admin.json()["code"] == "owner_auth_setup_required"

    ip_origin = client.post(
        "/api/computer/approvals/not-real/approve",
        headers={"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"},
    )
    assert ip_origin.status_code == 403
    assert ip_origin.json()["code"] == "owner_auth_origin_required"
    page = client.get("/owner-auth")
    assert page.status_code == 200 and "Owner passkey" in page.text
