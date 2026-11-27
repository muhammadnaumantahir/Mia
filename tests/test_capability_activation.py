import sqlite3

import pytest
from fastapi.testclient import TestClient

from mia.computer.capabilities import PermissionLevel
from mia.evolution.capabilities import (
    CapabilityActivationBlocked,
    CapabilityLifecycleError,
    CapabilityStore,
)
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

TESTS = "def test_ok():\n    assert True\n"


def registered(store, version, permissions=None, source=None):
    store.record_candidate(
        capability_id="hello-tool",
        version=version,
        name="Hello tool",
        source_code=source or f"def hello():\n    return '{version}'\n",
        test_source_code=TESTS,
        rationale="activation test",
        source_kind="generated",
        permissions=permissions or [],
    )
    candidate = store.get("hello-tool", version)
    store.begin_validation("hello-tool", version)
    store.record_validation(
        "hello-tool",
        version,
        {
            "static_checks": True,
            "unit_tests": True,
            "security_checks": True,
            "regression_tests": True,
            "evidence": {
                "runner": "docker-isolated-v1",
                "image_id": "sha256:x",
                "source_sha256": candidate["source_sha256"],
                "test_sha256": candidate["test_sha256"],
            },
        },
    )
    store.register("hello-tool", version)


def make_store(tmp_path, enabled=True):
    return CapabilityStore(
        tmp_path / "c.sqlite3", tmp_path / "generated", activation_enabled=enabled
    )


def promote(store, version):
    return store.activate("hello-tool", version, approved_by="owner", reason="reviewed diff")


def test_activation_is_blocked_unless_owner_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_CAPABILITY_ACTIVATION_ENABLED", raising=False)
    store = CapabilityStore(tmp_path / "c.sqlite3", tmp_path / "generated")
    registered(store, "0.1.0")
    with pytest.raises(CapabilityActivationBlocked):
        promote(store, "0.1.0")
    assert store.get("hello-tool", "0.1.0")["status"] == "registered"
    monkeypatch.setenv("MIA_CAPABILITY_ACTIVATION_ENABLED", "true")
    assert CapabilityStore(tmp_path / "d.sqlite3", tmp_path / "g2").activation_enabled


def test_activate_requires_registered_status_and_approval_details(tmp_path):
    store = make_store(tmp_path)
    store.record_candidate(
        capability_id="hello-tool", version="0.1.0", name="x", source_code="x = 1\n",
        test_source_code=TESTS, rationale="r", source_kind="generated", permissions=[],
    )
    with pytest.raises(CapabilityLifecycleError, match="registered"):
        promote(store, "0.1.0")  # only proposed
    registered(store, "0.2.0")
    with pytest.raises(ValueError):
        store.activate("hello-tool", "0.2.0", approved_by="", reason="why")
    with pytest.raises(ValueError):
        store.activate("hello-tool", "0.2.0", approved_by="owner", reason=" ")
    with pytest.raises(CapabilityLifecycleError, match="not found"):
        promote(store, "9.9.9")


def test_permissioned_capability_needs_a_broker_authorization_snapshot(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0", permissions=["filesystem.write"])
    with pytest.raises(CapabilityActivationBlocked, match="active owner grant"):
        promote(store, "0.1.0")

    activated = store.activate(
        "hello-tool",
        "0.1.0",
        approved_by="owner",
        reason="reviewed permission scope",
        permission_authorizations={"filesystem.write": "grant-id-from-owner-policy"},
    )
    assert activated["status"] == "active"
    event = store.list_events("hello-tool", "0.1.0")[-1]
    assert '"filesystem.write":"grant-id-from-owner-policy"' in event["payload_json"]


def test_api_permissioned_activation_requires_a_matching_live_owner_grant(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0", permissions=["filesystem.write"])
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "owner-grant-observatory.sqlite3"),
            capability_store=store,
        )
    )
    body = {"approved_by": "owner", "reason": "reviewed permission declaration"}

    denied = client.post("/api/capabilities/hello-tool/0.1.0/activate", json=body)
    assert denied.status_code == 403
    assert store.get_active("hello-tool") is None

    grant = client.app.state.permission_policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(tmp_path / "allowed-files"),
        kind="session",
    )
    activated = client.post("/api/capabilities/hello-tool/0.1.0/activate", json=body)
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    activation_event = store.list_events("hello-tool", "0.1.0")[-1]
    assert grant["grant_id"] in activation_event["payload_json"]

    client.app.state.permission_policy.revoke(grant["grant_id"])
    no_longer_authorized = client.post(
        "/api/capabilities/hello-tool/0.1.0/activate", json=body
    )
    assert no_longer_authorized.status_code == 403


def test_tampered_source_blocks_activation(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    path = store.get("hello-tool", "0.1.0")["source_path"]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("import os\n")
    with pytest.raises(CapabilityLifecycleError, match="hash"):
        promote(store, "0.1.0")
    assert store.get_active("hello-tool") is None


def test_promotion_deprecates_previous_and_keeps_single_active(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    registered(store, "0.2.0")
    promote(store, "0.1.0")
    promote(store, "0.2.0")
    assert store.get_active("hello-tool")["version"] == "0.2.0"
    assert store.get("hello-tool", "0.1.0")["status"] == "deprecated"
    event = store.list_events("hello-tool", "0.2.0")[-1]
    assert event["to_status"] == "active"
    assert '"previous_active":"0.1.0"' in event["payload_json"]
    db = sqlite3.connect(store.database_path)
    with pytest.raises(sqlite3.IntegrityError):  # the database itself forbids two actives
        db.execute(
            "UPDATE capability_versions SET status='active' WHERE version='0.1.0'"
        )
    db.close()


def test_rollback_restores_previous_and_rolled_back_is_terminal(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    registered(store, "0.2.0")
    promote(store, "0.1.0")
    promote(store, "0.2.0")
    result = store.rollback("hello-tool", approved_by="owner", reason="regression found")
    assert result == {
        "capability_id": "hello-tool",
        "rolled_back_version": "0.2.0",
        "restored_version": "0.1.0",
    }
    assert store.get_active("hello-tool")["version"] == "0.1.0"
    assert store.get("hello-tool", "0.2.0")["status"] == "rolled_back"
    with pytest.raises(CapabilityLifecycleError):
        promote(store, "0.2.0")  # a rolled-back version cannot return
    assert [e["to_status"] for e in store.list_events("hello-tool", "0.1.0")][-2:] == [
        "deprecated",
        "active",
    ]


def test_first_version_rollback_leaves_no_active_version(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    promote(store, "0.1.0")
    result = store.rollback("hello-tool", approved_by="owner", reason="bad first release")
    assert result["restored_version"] is None and store.get_active("hello-tool") is None
    with pytest.raises(CapabilityLifecycleError, match="no active"):
        store.rollback("hello-tool", approved_by="owner", reason="again")
    with pytest.raises(ValueError):
        store.rollback("hello-tool", approved_by="owner", reason="")


def test_api_promotion_and_rollback_are_audited(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    registered(store, "0.2.0")
    obs = ObservatoryStore(tmp_path / "o.sqlite3")
    client = TestClient(create_app(store=obs, capability_store=store))
    body = {"approved_by": "owner", "reason": "reviewed"}
    assert client.post("/api/capabilities/hello-tool/0.1.0/activate", json={}).status_code == 422
    assert client.post("/api/capabilities/hello-tool/0.1.0/activate", json=body).status_code == 200
    assert client.post("/api/capabilities/hello-tool/0.2.0/activate", json=body).status_code == 200
    assert client.get("/api/capabilities/hello-tool/active").json()["version"] == "0.2.0"
    rolled = client.post("/api/capabilities/hello-tool/rollback", json=body).json()
    assert rolled["restored_version"] == "0.1.0"
    assert client.get("/api/capabilities/hello-tool/active").json()["version"] == "0.1.0"
    types = [e["event_type"] for e in obs.list_events()]
    assert types.count("capability_activated") == 2 and "capability_rolled_back" in types


def test_api_activation_blocked_when_not_enabled(tmp_path):
    store = make_store(tmp_path, enabled=False)
    registered(store, "0.1.0")
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"),
                                   capability_store=store))
    response = client.post(
        "/api/capabilities/hello-tool/0.1.0/activate",
        json={"approved_by": "owner", "reason": "reviewed"},
    )
    assert response.status_code == 409
    assert client.get("/api/capabilities/hello-tool/active").status_code == 404


def test_api_enabled_activation_without_approval_body_is_rejected(tmp_path):
    store = make_store(tmp_path)
    registered(store, "0.1.0")
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"),
                                   capability_store=store))
    assert client.post("/api/capabilities/hello-tool/0.1.0/activate").status_code == 400
    assert store.get_active("hello-tool") is None


def test_dashboard_offers_confirmed_promotion_and_rollback():
    from pathlib import Path

    page = Path("src/mia/observatory/dashboard.html").read_text(encoding="utf-8")
    assert "promoteCapability" in page and "rollbackCapability" in page
    assert "window.confirm" in page and "/rollback" in page
    assert "Activation is intentionally blocked" not in page
    assert "capDependencies" in page and "capParent" in page
    assert "showCapabilityDiff" in page and "/diff" in page
