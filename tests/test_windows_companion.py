"""MIA-COMP-03: mocked Windows companion lifecycle and outbound protocol tests."""

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mia.companion.agent import CompanionAgent, CompanionAgentError, validate_server_url
from mia.companion.dpapi import CompanionSecretProtectionError, WindowsDPAPI
from mia.companion.identity import CompanionIdentity
from mia.companion.lifecycle import (
    CompanionLifecycleError,
    WindowsCompanionLifecycle,
)
from mia.security.pairing import auth_statement, b64e, pairing_statement


def decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class TestProtector:
    """Reversible test double; production storage uses Windows CurrentUser DPAPI."""

    def protect(self, value):
        return b"protected:" + value[::-1]

    def unprotect(self, value):
        if not value.startswith(b"protected:"):
            raise ValueError("not protected")
        return value[len(b"protected:"):][::-1]


class FakeClock:
    def __init__(self):
        self.value = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://127.0.0.1:8765/test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self):
        return self.payload


class FakeCompanionServer:
    def __init__(self, *, on_heartbeat=None, redirect_pair=False):
        self.device_id = "device-1234567890"
        self.device_name = "Mia Dev Laptop"
        self.scopes = ["companion.heartbeat", "companion.observe"]
        self.private_key = None
        self.public_key = None
        self.challenge_counter = 0
        self.active_token = None
        self.expires_at = None
        self.calls = []
        self.on_heartbeat = on_heartbeat
        self.redirect_pair = redirect_pair

    def request(self, method, url, *, json=None, headers=None):
        path = url.split("8765", 1)[-1]
        headers = headers or {}
        self.calls.append((method, path, json, dict(headers)))
        if path == "/api/companion/pair":
            if self.redirect_pair:
                return FakeResponse({}, 302)
            self.public_key = json["public_key"]
            self.device_name = json["device_name"]
            signed = Ed25519PublicKey.from_public_bytes(decode(self.public_key))
            signed.verify(
                decode(json["signature"]),
                pairing_statement(json["code"], self.public_key, self.device_name),
            )
            return FakeResponse({
                "device_id": self.device_id,
                "name": self.device_name,
                "scopes": self.scopes,
                "created_at": "2026-11-26T12:00:00+00:00",
            }, 201)
        if path == "/api/companion/challenge":
            self.challenge_counter += 1
            return FakeResponse({
                "device_id": json["device_id"],
                "challenge_id": f"challenge-{self.challenge_counter}",
                "nonce": f"nonce-{self.challenge_counter}",
            })
        if path == "/api/companion/session":
            signed = Ed25519PublicKey.from_public_bytes(decode(self.public_key))
            signed.verify(
                decode(json["signature"]),
                auth_statement(json["device_id"], json["challenge_id"], f"nonce-{self.challenge_counter}"),
            )
            self.active_token = f"mia-dev_session-{self.challenge_counter}"
            self.expires_at = "2026-11-26T12:15:00+00:00"
            return FakeResponse({
                "device_id": self.device_id,
                "token": self.active_token,
                "expires_at": self.expires_at,
                "scopes": self.scopes,
            })
        if path == "/api/companion/heartbeat":
            assert headers.get("Authorization") == f"Bearer {self.active_token}"
            if self.on_heartbeat:
                self.on_heartbeat()
            return FakeResponse({"status": "ok"})
        raise AssertionError(f"unexpected companion request: {method} {path}")


def make_identity(tmp_path):
    return CompanionIdentity(tmp_path / "companion", protector=TestProtector())


def pair_agent(tmp_path, *, clock=None, server=None):
    server = server or FakeCompanionServer()
    identity = make_identity(tmp_path)
    agent = CompanionAgent(
        identity, client=server, clock=clock or FakeClock(),
        heartbeat_seconds=10,
    )
    status = agent.pair(
        "http://127.0.0.1:8765", "ABCD2345EF", "Mia Dev Laptop"
    )
    return agent, identity, server, status


def test_server_url_requires_loopback_http_or_https():
    assert validate_server_url("http://127.0.0.1:8765/") == "http://127.0.0.1:8765"
    assert validate_server_url("https://mia.example.com:8443") == "https://mia.example.com:8443"
    for value in (
        "http://mia.example.com", "http://user:pass@127.0.0.1:8765",
        "https://mia.example.com/path", "https://mia.example.com/?x=1",
        "ftp://127.0.0.1", "not a url",
    ):
        with pytest.raises(CompanionAgentError):
            validate_server_url(value)


def test_pairing_uses_ed25519_and_encrypts_private_key_and_session_token(tmp_path):
    agent, identity, server, status = pair_agent(tmp_path)
    public_file = identity.path.read_bytes()
    assert status["paired"] is True
    assert status["device_id"] == server.device_id
    assert status["scopes"] == server.scopes
    assert b"mia-dev_session" not in public_file
    assert server.public_key.encode() not in public_file
    assert b"protected:" in public_file
    loaded = identity.load()
    assert loaded["session_token"] == server.active_token
    assert len(loaded["private_key"]) == 32
    assert not any("Authorization" in call[3] for call in server.calls)
    agent.close()


def test_first_pairing_is_loopback_only_even_when_server_uses_https(tmp_path):
    identity = make_identity(tmp_path)
    agent = CompanionAgent(identity, client=FakeCompanionServer())
    with pytest.raises(CompanionAgentError, match="loopback"):
        agent.pair("https://mia.example.com", "ABCD2345EF", "Mia laptop")


def test_pending_pairing_key_can_recover_after_first_session_failure(tmp_path):
    class SessionInterruptedServer(FakeCompanionServer):
        def request(self, method, url, *, json=None, headers=None):
            if url.endswith("/api/companion/session"):
                request = httpx.Request("POST", url)
                response = httpx.Response(503, request=request)
                raise httpx.HTTPStatusError("offline", request=request, response=response)
            return super().request(method, url, json=json, headers=headers)

    server = SessionInterruptedServer()
    identity = make_identity(tmp_path)
    agent = CompanionAgent(identity, client=server, clock=FakeClock())
    with pytest.raises(CompanionAgentError, match="HTTP 503"):
        agent.pair("http://127.0.0.1:8765", "ABCD2345EF", "Mia Dev Laptop")
    assert identity.exists
    assert identity.load()["session_token"] == ""
    # Simulate a service restart and retry the locally allowed signed challenge.
    recovered = CompanionAgent(identity, client=server, clock=FakeClock())
    result = recovered._new_session(identity.load())
    assert result["session_token"].startswith("mia-dev_")


def test_session_renews_before_expiry_and_heartbeat_uses_new_token(tmp_path):
    clock = FakeClock()
    agent, identity, server, _ = pair_agent(tmp_path, clock=clock)
    clock.advance(14 * 60)
    result = agent.poll_once()
    assert result["status"] == "ok"
    assert server.challenge_counter == 2
    assert identity.load()["session_token"] == server.active_token
    assert any(call[1] == "/api/companion/heartbeat" for call in server.calls)
    agent.close()


def test_remote_expired_session_requires_local_repair(tmp_path):
    clock = FakeClock()
    agent, identity, server, _ = pair_agent(tmp_path, clock=clock)
    data = identity.load()
    data["server_url"] = "https://mia.example.com"
    data["expires_at"] = (clock() - timedelta(seconds=1)).isoformat()
    identity.save(data)
    with pytest.raises(CompanionAgentError, match="expired while disconnected"):
        agent._new_session(identity.load())
    agent.close()


def test_redirects_are_refused_and_proxy_environment_is_not_used(tmp_path):
    server = FakeCompanionServer(redirect_pair=True)
    agent = CompanionAgent(make_identity(tmp_path), client=server)
    with pytest.raises(CompanionAgentError, match="redirect"):
        agent.pair("http://127.0.0.1:8765", "ABCD2345EF", "Mia laptop")


def test_run_loop_exits_after_graceful_stop_signal(tmp_path):
    identity = make_identity(tmp_path)
    server = FakeCompanionServer()
    agent = CompanionAgent(
        identity, client=server, clock=FakeClock(), heartbeat_seconds=10,
        sleep=lambda _seconds: None,
    )
    agent.pair("http://127.0.0.1:8765", "ABCD2345EF", "Mia laptop")
    server.on_heartbeat = lambda: agent.stop_path.write_text("stop", encoding="ascii")
    assert agent.run_forever() == 0
    status = json.loads(agent.status_path.read_text(encoding="utf-8"))
    assert status["state"] == "stopped"
    assert status["inbound_listener"] is False
    agent.close()


def test_dpapi_has_no_plaintext_fallback_off_windows(monkeypatch):
    monkeypatch.setattr("mia.companion.dpapi.sys.platform", "linux")
    with pytest.raises(CompanionSecretProtectionError, match="DPAPI"):
        WindowsDPAPI().protect(b"private-key")


class FakeTaskScheduler:
    def __init__(self, installed=False):
        self.installed = installed
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        if "/Create" in args:
            self.installed = True
        elif "/Delete" in args:
            self.installed = False
        if "/Query" in args and not self.installed:
            return SimpleNamespace(returncode=1, stdout="", stderr="not found")
        return SimpleNamespace(returncode=0, stdout="OK", stderr="")


def test_install_requires_confirmation_and_uses_limited_current_user_task(tmp_path):
    identity = make_identity(tmp_path)
    agent, identity, _server, _status = pair_agent(tmp_path)
    scheduler = FakeTaskScheduler()
    manager = WindowsCompanionLifecycle(
        identity, platform_name="win32", run=scheduler
    )
    with pytest.raises(CompanionLifecycleError, match="--confirm"):
        manager.install()
    result = manager.install(confirmed=True)
    assert result["installed"] is True
    create = next(call for call in scheduler.calls if "/Create" in call)
    assert "LIMITED" in create
    assert "/IT" in create
    assert "HIGHEST" not in create
    assert "/RU" not in create
    agent.close()


def test_uninstall_requires_confirmation_and_removes_only_named_task(tmp_path):
    agent, identity, _server, _status = pair_agent(tmp_path)
    scheduler = FakeTaskScheduler(installed=True)
    manager = WindowsCompanionLifecycle(
        identity, platform_name="win32", run=scheduler,
        process_alive=lambda _pid: False,
    )
    with pytest.raises(CompanionLifecycleError, match="--confirm"):
        manager.uninstall()
    result = manager.uninstall(confirmed=True)
    assert result["uninstalled"] is True
    assert any("/Delete" in call and "Mia Companion" in call for call in scheduler.calls)
    agent.close()


def test_start_uses_existing_current_user_task_when_installed(tmp_path):
    agent, identity, _server, _status = pair_agent(tmp_path)
    scheduler = FakeTaskScheduler(installed=True)
    manager = WindowsCompanionLifecycle(
        identity, platform_name="win32", run=scheduler
    )
    result = manager.start()
    assert result["scheduled_task"] is True
    assert any("/Run" in call and "Mia Companion" in call for call in scheduler.calls)
    agent.close()


def test_stop_is_graceful_and_never_invokes_force_termination(tmp_path):
    agent, identity, _server, _status = pair_agent(tmp_path)
    scheduler = FakeTaskScheduler()
    ticks = [0.0]

    def monotonic():
        ticks[0] += 1.0
        return ticks[0]

    manager = WindowsCompanionLifecycle(
        identity, platform_name="win32", run=scheduler,
        process_alive=lambda _pid: True, monotonic=monotonic,
        sleep=lambda _seconds: None,
    )
    manager.status_path.parent.mkdir(parents=True, exist_ok=True)
    manager.status_path.write_text(json.dumps({"pid": 777, "state": "online"}), encoding="utf-8")
    assert manager.stop(timeout_seconds=3) is False
    assert manager.stop_path.exists()
    assert not any("/End" in call for call in scheduler.calls)
    agent.close()


def test_status_does_not_expose_bearer_or_private_key(tmp_path):
    agent, identity, _server, _status = pair_agent(tmp_path)
    manager = WindowsCompanionLifecycle(
        identity, platform_name="win32", run=FakeTaskScheduler(),
        process_alive=lambda _pid: False,
    )
    result = manager.status()
    rendered = json.dumps(result)
    assert "mia-dev_" not in rendered
    assert "private_key" not in rendered
    assert result["outbound_only"] is True
    assert result["elevated"] is False
    agent.close()
