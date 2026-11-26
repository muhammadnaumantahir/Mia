"""MIA-COMP-02: outbound-only companion pairing, replay protection, scopes, revocation, confinement.

Real Ed25519 keys, real SQLite, real FastAPI app. The clock is injected only where expiry is tested.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from mia.observatory.api import create_app  # noqa: E402
from mia.observatory.store import ObservatoryStore  # noqa: E402
from mia.security.pairing import (  # noqa: E402
    ALLOWED_SCOPES,
    CODE_LENGTH,
    MAX_FAILURES,
    PairingError,
    PairingService,
    ScopeError,
    auth_statement,
    b64e,
    pairing_statement,
)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 25, 6, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


class Device:
    """A stand-in companion: owns a private key that never leaves this object."""

    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = b64e(self.key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        self.device_id = None

    def sign(self, message: bytes) -> str:
        return b64e(self.key.sign(message))

    def pair_body(self, code, name="Desk PC"):
        return {"code": code, "public_key": self.public, "device_name": name,
                "signature": self.sign(pairing_statement(code, self.public, name))}

    def session_body(self, challenge):
        return {"device_id": self.device_id, "challenge_id": challenge["challenge_id"],
                "signature": self.sign(auth_statement(self.device_id, challenge["challenge_id"], challenge["nonce"]))}


@pytest.fixture
def svc(tmp_path):
    clock = Clock()
    return PairingService(tmp_path / "p.sqlite3", clock=clock), clock


def paired(service, scopes=("companion.heartbeat",), device=None):
    device = device or Device()
    code = service.create_code("Desk PC", list(scopes))["code"]
    device.device_id = service.redeem(**device.pair_body(code))["device_id"]
    return device


def login(service, device):
    return service.open_session(**device.session_body(service.challenge(device.device_id)))


# ---- pairing codes ------------------------------------------------------------------------
def test_code_is_single_use_and_stored_hashed(svc, tmp_path):
    service, _ = svc
    code = service.create_code("Desk PC", ["companion.heartbeat"])["code"]
    assert len(code) == CODE_LENGTH
    import sqlite3
    raw = sqlite3.connect(tmp_path / "p.sqlite3").execute("SELECT code_hash FROM pairing_codes").fetchone()[0]
    assert code not in raw and len(raw) == 64
    first = Device()
    assert service.redeem(**first.pair_body(code))["status"] == "active"
    with pytest.raises(PairingError):
        service.redeem(**Device().pair_body(code))


def test_code_expires(svc):
    service, clock = svc
    code = service.create_code("Desk PC", ["companion.heartbeat"], ttl_seconds=60)["code"]
    clock.advance(seconds=61)
    with pytest.raises(PairingError):
        service.redeem(**Device().pair_body(code))
    assert service.list_devices() == []


def test_redeem_requires_proof_of_the_private_key(svc):
    service, _ = svc
    code = service.create_code("Desk PC", ["companion.heartbeat"])["code"]
    thief, owner = Device(), Device()
    body = owner.pair_body(code)
    body["signature"] = thief.sign(pairing_statement(code, owner.public, "Desk PC"))  # someone else's signature
    with pytest.raises(PairingError):
        service.redeem(**body)
    tampered = owner.pair_body(code)
    tampered["device_name"] = "Other name"  # signature no longer covers the statement
    with pytest.raises(PairingError):
        service.redeem(**tampered)
    assert service.list_devices() == []
    service.redeem(**owner.pair_body(code))  # a failed attempt did not burn the code


def test_guessing_codes_is_locked_out_even_for_the_right_code(svc):
    service, clock = svc
    real = service.create_code("Desk PC", ["companion.heartbeat"])["code"]
    attacker = Device()
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairingError):
            service.redeem(**attacker.pair_body("A" * CODE_LENGTH))
    with pytest.raises(PairingError) as exc:
        service.redeem(**Device().pair_body(real))
    assert exc.value.locked
    clock.advance(seconds=301)  # lockout lifts; the old code has expired too (same 5 minutes)
    with pytest.raises(PairingError):
        service.redeem(**Device().pair_body(real))
    fresh = service.create_code("Desk PC", ["companion.heartbeat"])["code"]
    assert service.redeem(**Device().pair_body(fresh))["status"] == "active"


def test_scopes_are_validated_and_approval_is_never_grantable(svc):
    service, _ = svc
    for bad in (["approve"], ["companion.heartbeat", "computer.approve"], [], ["permissions.grant"]):
        with pytest.raises(ValueError):
            service.create_code("x", bad)
    assert not any("approv" in s or "grant" in s or "pair" in s for s in ALLOWED_SCOPES)
    with pytest.raises(ValueError):
        service.create_code("x", ["companion.heartbeat"], ttl_seconds=3600)


def test_same_key_cannot_be_enrolled_twice(svc):
    service, _ = svc
    device = paired(service)
    code = service.create_code("Second", ["companion.act"])["code"]
    with pytest.raises(PairingError):
        service.redeem(**device.pair_body(code, "Clone"))
    assert len(service.list_devices()) == 1


# ---- challenge / session ------------------------------------------------------------------
def test_login_issues_scoped_short_lived_token_and_stores_only_its_hash(svc, tmp_path):
    service, clock = svc
    device = paired(service, ("companion.heartbeat", "companion.observe"))
    session = login(service, device)
    caller = service.verify_token(session["token"])
    assert caller.device_id == device.device_id and caller.scopes == {"companion.heartbeat", "companion.observe"}
    import sqlite3
    stored = [r[0] for r in sqlite3.connect(tmp_path / "p.sqlite3").execute("SELECT token_hash FROM sessions")]
    assert session["token"] not in stored and all(len(h) == 64 for h in stored)
    clock.advance(seconds=901)
    with pytest.raises(PairingError):
        service.verify_token(session["token"])


def test_challenge_is_single_use_so_a_captured_login_cannot_be_replayed(svc):
    service, _ = svc
    device = paired(service)
    challenge = service.challenge(device.device_id)
    body = device.session_body(challenge)
    assert service.open_session(**body)["token"]
    with pytest.raises(PairingError):
        service.open_session(**body)  # exact replay of a valid login


def test_expired_challenge_and_wrong_device_and_forged_signature_all_fail_the_same_way(svc):
    service, clock = svc
    device, other = paired(service), paired(service, device=Device())
    old = service.challenge(device.device_id)
    clock.advance(seconds=31)
    with pytest.raises(PairingError) as expired:
        service.open_session(**device.session_body(old))
    fresh = service.challenge(device.device_id)
    forged = device.session_body(fresh)
    forged["signature"] = Device().sign(auth_statement(device.device_id, fresh["challenge_id"], fresh["nonce"]))
    with pytest.raises(PairingError) as bad_sig:
        service.open_session(**forged)
    swapped = service.challenge(device.device_id)  # challenge issued for A, presented by B
    with pytest.raises(PairingError) as wrong_dev:
        service.open_session(other.device_id, swapped["challenge_id"], other.sign(auth_statement(other.device_id, swapped["challenge_id"], swapped["nonce"])))
    with pytest.raises(PairingError) as unknown:
        service.open_session("nobody", service.challenge("nobody")["challenge_id"], "AAAA")
    assert {str(e.value) for e in (expired, bad_sig, wrong_dev, unknown)} == {"pairing or authentication failed"}


def test_a_failed_login_still_burns_the_challenge(svc):
    service, _ = svc
    device = paired(service)
    challenge = service.challenge(device.device_id)
    bad = device.session_body(challenge)
    bad["signature"] = "AAAA"
    with pytest.raises(PairingError):
        service.open_session(**bad)
    with pytest.raises(PairingError):
        service.open_session(**device.session_body(challenge))


def test_repeated_failed_logins_lock_that_device_only(svc):
    service, _ = svc
    device, other = paired(service), paired(service, device=Device())
    for _ in range(MAX_FAILURES):
        with pytest.raises(PairingError):
            service.open_session(device.device_id, service.challenge(device.device_id)["challenge_id"], "AAAA")
    with pytest.raises(PairingError) as exc:
        login(service, device)
    assert exc.value.locked
    assert login(service, other)["token"]


# ---- scopes and revocation ----------------------------------------------------------------
def test_scope_is_enforced(svc):
    service, _ = svc
    device = paired(service, ("companion.heartbeat",))
    token = login(service, device)["token"]
    assert service.verify_token(token, scope="companion.heartbeat")
    with pytest.raises(ScopeError):
        service.verify_token(token, scope="companion.act")


def test_revocation_ends_live_sessions_and_blocks_new_ones(svc):
    service, _ = svc
    device = paired(service)
    token = login(service, device)["token"]
    assert service.revoke(device.device_id) is True
    with pytest.raises(PairingError):
        service.verify_token(token)
    with pytest.raises(PairingError):
        login(service, device)
    assert service.revoke(device.device_id) is False
    assert service.list_devices()[0]["status"] == "revoked"


def test_revoke_all_clears_devices_sessions_and_unused_codes(svc):
    service, _ = svc
    a, b = paired(service), paired(service, device=Device())
    tokens = [login(service, d)["token"] for d in (a, b)]
    pending = service.create_code("Later", ["companion.heartbeat"])["code"]
    assert service.revoke_all() == 2
    for token in tokens:
        with pytest.raises(PairingError):
            service.verify_token(token)
    with pytest.raises(PairingError):
        service.redeem(**Device().pair_body(pending))


def test_tokens_must_look_like_device_tokens(svc):
    service, _ = svc
    for bad in ("", "Bearer x", "abc", None):
        with pytest.raises(PairingError):
            service.verify_token(bad)


# ---- HTTP surface -------------------------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"))
    return TestClient(app), app


def http_pair(client, device, scopes=("companion.heartbeat",)):
    code = client.post("/api/companion-admin/pairing-codes", json={"label": "Desk PC", "scopes": list(scopes)}).json()["code"]
    r = client.post("/api/companion/pair", json=device.pair_body(code))
    assert r.status_code == 201, r.text
    device.device_id = r.json()["device_id"]
    return device


def http_login(client, device):
    challenge = client.post("/api/companion/challenge", json={"device_id": device.device_id}).json()
    r = client.post("/api/companion/session", json=device.session_body(challenge))
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_http_full_flow_and_heartbeat_updates_last_seen(api):
    client, _ = api
    device = http_pair(client, Device())
    headers = http_login(client, device)
    assert client.get("/api/companion/whoami", headers=headers).json()["scopes"] == ["companion.heartbeat"]
    assert client.post("/api/companion/heartbeat", headers=headers).json() == {"status": "ok"}
    listed = client.get("/api/companion-admin/devices").json()["items"]
    assert listed[0]["last_seen"] and "public_key" not in listed[0] and listed[0]["key_fingerprint"]


def test_http_missing_or_bad_bearer_and_missing_scope(api):
    client, _ = api
    device = http_pair(client, Device(), scopes=("companion.observe",))
    assert client.get("/api/companion/whoami").status_code == 401
    assert client.get("/api/companion/whoami", headers={"Authorization": "Bearer mia-dev_nope"}).status_code == 401
    assert client.post("/api/companion/heartbeat", headers=http_login(client, device)).status_code == 403


def test_a_device_token_cannot_reach_owner_routes_including_approvals_and_pairing(api):
    client, _ = api
    device = http_pair(client, Device(), scopes=tuple(sorted(ALLOWED_SCOPES)))
    headers = http_login(client, device)
    attempts = [
        ("POST", "/api/computer/approvals/" + "a" * 32 + "/approve"),
        ("POST", "/api/computer/permissions"),
        ("POST", "/api/computer/permissions/revoke-all"),
        ("POST", "/api/companion-admin/pairing-codes"),
        ("POST", "/api/companion-admin/revoke-all"),
        ("GET", "/api/companion-admin/devices"),
        ("POST", "/api/computer/stop-all"),
        ("GET", "/api/memory/items"),
    ]
    for method, path in attempts:
        r = client.request(method, path, headers=headers, json={})
        assert r.status_code == 403 and "Device credentials" in r.json()["detail"], (method, path, r.status_code)
    assert len(client.get("/api/companion-admin/devices").json()["items"]) == 1  # owner (no token) still works


def test_http_revoke_cuts_off_an_active_session_immediately(api):
    client, _ = api
    device = http_pair(client, Device())
    headers = http_login(client, device)
    assert client.post(f"/api/companion-admin/devices/{device.device_id}/revoke").json() == {"revoked": True}
    assert client.get("/api/companion/whoami", headers=headers).status_code == 401
    challenge = client.post("/api/companion/challenge", json={"device_id": device.device_id}).json()
    assert client.post("/api/companion/session", json=device.session_body(challenge)).status_code == 401


def test_http_lockout_returns_429_and_errors_do_not_reveal_which_part_failed(api):
    client, _ = api
    attacker = Device()
    statuses = [client.post("/api/companion/pair", json=attacker.pair_body("B" * CODE_LENGTH)).status_code
                for _ in range(MAX_FAILURES + 2)]
    assert statuses[:MAX_FAILURES] == [401] * MAX_FAILURES and statuses[-1] == 429
    r = client.post("/api/companion/session", json={"device_id": "x", "challenge_id": "y", "signature": "z"})
    assert r.status_code == 401 and r.json()["detail"] == "pairing or authentication failed"


def test_http_rejects_extra_fields_and_bad_scopes(api):
    client, _ = api
    assert client.post("/api/companion-admin/pairing-codes", json={"label": "x", "scopes": ["approve"]}).status_code == 422
    assert client.post("/api/companion-admin/pairing-codes", json={"label": "x", "scopes": ["companion.act"], "admin": True}).status_code == 422
    assert client.post("/api/companion/pair", json={"code": "x", "public_key": "x", "device_name": "x", "signature": "x", "scopes": ["companion.act"]}).status_code == 422


def test_pairing_is_audited_without_secrets(api):
    client, app = api
    device = http_pair(client, Device())
    headers = http_login(client, device)
    events = app.state.observatory.list_events() if hasattr(app.state.observatory, "list_events") else []
    text = str(events)
    assert "companion_paired" in text
    assert headers["Authorization"].split(" ", 1)[1] not in text and device.public not in text


def test_no_companion_route_opens_an_inbound_control_channel(api):
    _, app = api
    from fastapi.routing import APIWebSocketRoute

    companion = [r for r in app.routes if getattr(r, "path", "").startswith(("/api/companion/", "/api/companion-admin/"))]
    assert companion and not any(isinstance(r, APIWebSocketRoute) for r in companion)


def test_token_is_rechecked_against_the_device_record_on_every_use(svc, tmp_path):
    """Defence in depth: even if a session row survives (crash or race mid-revoke), a revoked device fails."""
    import sqlite3

    service, _ = svc
    device = paired(service)
    token = login(service, device)["token"]
    db = sqlite3.connect(tmp_path / "p.sqlite3")
    db.execute("UPDATE devices SET status='revoked' WHERE device_id=?", (device.device_id,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1, "the session row is still there"
    with pytest.raises(PairingError):
        service.verify_token(token)

def test_companion_devices_owner_page_is_served(api):
    client, _ = api
    response = client.get("/companion")
    assert response.status_code == 200
    assert "Companion Devices" in response.text
    assert "one-time pairing code" in response.text
    assert "not yet cryptographically authenticated" in response.text
