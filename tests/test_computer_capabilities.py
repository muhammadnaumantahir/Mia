from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mia.computer.capabilities import (
    CapabilityRegistry,
    CapabilitySpec,
    PermissionLevel,
    RiskLevel,
    default_registry,
)
from mia.computer.environment import detect_environment
from mia.computer.permissions import PermissionError_, PermissionPolicy
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def _fake_platform(system="Windows"):
    return SimpleNamespace(
        system=lambda: system, release=lambda: "11", version=lambda: "10.0.22631",
        machine=lambda: "AMD64",
    )


def _spec(name="x.y", perm=PermissionLevel.WRITE, risk=RiskLevel.MEDIUM, **kw):
    return CapabilitySpec(
        name, "d", perm, risk, 5, "verify",
        {"type": "object"}, {"type": "object"}, **kw,
    )


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self):
        return self.now


def test_environment_detection_simulates_windows():
    env = detect_environment(
        platform_module=_fake_platform("Windows"),
        environ={}, which=lambda n: f"C:/{n}.exe" if n in {"powershell", "cmd"} else None,
        disk_usage=lambda r: SimpleNamespace(total=100, free=40),
        exists=lambda p: p in {"C:\\", "D:\\"},
        psutil_module=SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=8 * 2**30)),
    )
    assert env.is_windows and env.os_release == "11" and env.machine == "AMD64"
    assert env.shells == ("powershell", "cmd")
    assert [d["root"] for d in env.disks] == ["C:\\", "D:\\"]
    assert env.total_memory_bytes == 8 * 2**30


def test_environment_memory_unknown_without_psutil():
    class Broken:
        def virtual_memory(self):
            raise RuntimeError

    env = detect_environment(platform_module=_fake_platform("Linux"), environ={},
                             psutil_module=Broken())
    assert env.os_family == "linux" and env.total_memory_bytes is None
    assert env.has_display is False


def test_registry_validates_and_rejects_duplicates():
    reg = CapabilityRegistry()
    reg.register(_spec())
    with pytest.raises(ValueError):
        reg.register(_spec())
    with pytest.raises(ValueError):
        _spec(name="nodot")
    with pytest.raises(ValueError):
        CapabilitySpec("a.b", "d", PermissionLevel.READ, RiskLevel.LOW, 0, "v",
                       {"type": "object"}, {"type": "object"})
    with pytest.raises(ValueError):
        CapabilitySpec("a.b", "d", PermissionLevel.READ, RiskLevel.LOW, 5, "v",
                       {"type": "array"}, {"type": "object"})
    with pytest.raises(ValueError):
        _spec(supported_os=("beos",))


def test_default_registry_declares_complete_contracts_and_windows_only_gating():
    reg = default_registry()
    for spec in reg.specs():
        d = spec.describe()
        assert d["verification"] and d["timeout_seconds"] > 0 and d["supported_os"]
        assert spec.to_contract().tool_id == spec.name
    linux = detect_environment(platform_module=_fake_platform("Linux"), environ={})
    assert not reg.check("desktop.list_windows", linux).available
    assert "windows" in reg.check("desktop.list_windows", linux).reason or "linux" in (
        reg.check("desktop.list_windows", linux).reason
    )
    assert reg.check("filesystem.read", linux).available
    assert [s.name for s in reg.specs(os_family="linux") if s.name.startswith("desktop")] == []


def test_health_check_failure_is_reported_not_raised():
    reg = CapabilityRegistry()
    def boom(env):
        raise RuntimeError("secret detail")
    reg.register(_spec(health_check=boom))
    env = detect_environment(platform_module=_fake_platform("Linux"), environ={})
    result = reg.check("x.y", env)
    assert not result.available and "secret detail" not in result.reason


def test_policy_defaults_and_grants(tmp_path):
    clock = Clock()
    policy = PermissionPolicy(tmp_path / "p.sqlite3", clock=clock)
    read = _spec("filesystem.read", PermissionLevel.READ, RiskLevel.LOW)
    write = _spec("filesystem.write", PermissionLevel.WRITE, RiskLevel.MEDIUM)
    other = _spec("filesystem.move", PermissionLevel.WRITE, RiskLevel.MEDIUM)
    assert not policy.authorize(read).requires_approval
    assert policy.authorize(write).requires_approval

    policy.grant(granted_by="owner", capabilities=["filesystem.write"],
                 level=PermissionLevel.WRITE, directory="/work/proj", duration_seconds=60)
    assert not policy.authorize(write, path="/work/proj/a/b.txt").requires_approval
    assert policy.authorize(write, path="/work/project2/b.txt").requires_approval  # prefix trap
    assert policy.authorize(write, path="/work/other").requires_approval
    assert policy.authorize(write).requires_approval  # no path given for a directory grant
    assert policy.authorize(other, path="/work/proj/x").requires_approval  # unrelated capability

    clock.now += timedelta(seconds=61)
    assert policy.authorize(write, path="/work/proj/a.txt").requires_approval  # expired


def test_policy_level_too_low_and_revocation(tmp_path):
    policy = PermissionPolicy(tmp_path / "p.sqlite3")
    run = _spec("terminal.run", PermissionLevel.EXECUTE, RiskLevel.MEDIUM)
    g = policy.grant(granted_by="owner", capabilities=["terminal.run"],
                     level=PermissionLevel.WRITE, duration_seconds=100)
    assert policy.authorize(run).requires_approval  # WRITE grant does not cover EXECUTE
    g2 = policy.grant(granted_by="owner", capabilities=["terminal.run"],
                      level=PermissionLevel.EXECUTE, duration_seconds=100)
    assert not policy.authorize(run).requires_approval
    assert policy.revoke(g2["grant_id"]) and not policy.revoke(g2["grant_id"])
    assert policy.authorize(run).requires_approval
    assert policy.revoke_all() == 1 and g["grant_id"]
    assert policy.active_grants() == []


def test_high_risk_and_admin_never_covered_by_normal_grants(tmp_path):
    policy = PermissionPolicy(tmp_path / "p.sqlite3")
    kill = _spec("process.stop", PermissionLevel.EXECUTE, RiskLevel.HIGH)
    policy.grant(granted_by="owner", capabilities=["process.stop"],
                 level=PermissionLevel.EXECUTE, duration_seconds=100)
    d = policy.authorize(kill)
    assert d.allowed and d.requires_approval

    admin = _spec("system.service_stop", PermissionLevel.ADMINISTRATIVE, RiskLevel.CRITICAL)
    assert policy.authorize(admin).allowed is False
    with pytest.raises(PermissionError_):
        policy.grant(granted_by="owner", capabilities=["system.service_stop"],
                     level=PermissionLevel.ADMINISTRATIVE, duration_seconds=10)
    with pytest.raises(PermissionError_):
        policy.grant(granted_by="owner", capabilities=["*"],
                     level=PermissionLevel.ADMINISTRATIVE, admin=True, duration_seconds=10)
    policy.grant(granted_by="owner", capabilities=["system.service_stop"],
                 level=PermissionLevel.ADMINISTRATIVE, admin=True, duration_seconds=10)
    d = policy.authorize(admin)
    assert d.allowed and d.requires_approval


def test_model_cannot_grant_and_validation(tmp_path):
    policy = PermissionPolicy(tmp_path / "p.sqlite3")
    for actor in ("model", "assistant", "ai", ""):
        with pytest.raises(PermissionError_):
            policy.grant(granted_by=actor, capabilities=["filesystem.write"],
                         level=PermissionLevel.WRITE, duration_seconds=10)
    assert policy.active_grants() == []
    with pytest.raises(PermissionError_):
        policy.grant(granted_by="owner", capabilities=["filesystem.write"],
                     level=PermissionLevel.WRITE, kind="persistent")  # persistent needs expiry
    assert any(h["outcome"] == "denied" for h in policy.history())


def test_session_grants_do_not_survive_restart_but_persistent_do(tmp_path):
    db = tmp_path / "p.sqlite3"
    first = PermissionPolicy(db)
    first.grant(granted_by="owner", capabilities=["a.b"], level=PermissionLevel.WRITE,
                kind="session", duration_seconds=1000)
    first.grant(granted_by="owner", capabilities=["c.d"], level=PermissionLevel.WRITE,
                kind="persistent", duration_seconds=1000)
    second = PermissionPolicy(db)
    assert [g["capabilities"] for g in second.active_grants()] == [["c.d"]]


def test_capability_and_permission_api(tmp_path):
    policy = PermissionPolicy(tmp_path / "perm.sqlite3")
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "obs.sqlite3"), permission_policy=policy))
    env = client.get("/api/computer/environment").json()
    assert env["os_family"] in {"windows", "linux", "macos", "other"}

    caps = client.get("/api/computer/capabilities").json()
    names = {c["name"]: c for c in caps["items"]}
    assert "filesystem.write" in names and names["filesystem.write"]["verification"]
    assert "desktop.type_text" in names

    health = client.get("/api/computer/health").json()["items"]
    assert {h["name"] for h in health} == set(names)

    bad = client.post("/api/computer/permissions", json={
        "capabilities": ["nope.nothing"], "level": "write", "duration_seconds": 60})
    assert bad.status_code == 422
    extra = client.post("/api/computer/permissions", json={
        "capabilities": ["filesystem.write"], "level": "write", "granted_by": "model"})
    assert extra.status_code == 422

    ok = client.post("/api/computer/permissions", json={
        "capabilities": ["filesystem.write"], "level": "write", "duration_seconds": 60})
    assert ok.status_code == 201
    gid = ok.json()["grant_id"]
    assert len(client.get("/api/computer/permissions").json()["items"]) == 1
    assert client.delete(f"/api/computer/permissions/{gid}").status_code == 200
    assert client.delete(f"/api/computer/permissions/{gid}").status_code == 404
    assert client.get("/api/computer/permissions").json()["items"] == []
    assert client.post("/api/computer/permissions/revoke-all").json()["count"] == 0
    assert client.get("/api/computer/permissions/history").json()["items"]
