from fastapi.testclient import TestClient

from mia.evolution.capabilities import CapabilityStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def test_capability_api_requires_all_validation_gates_before_registration(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
        )
    )

    created = client.post(
        "/api/capabilities",
        json={
            "capability_id": "hello-tool",
            "version": "0.1.0",
            "name": "Hello tool",
            "source_code": "def hello():\n    return 'hello'\n",
            "rationale": "Add a small, testable example capability.",
            "source_kind": "generated",
            "permissions": [],
        },
    )
    assert created.status_code == 201
    assert created.json()["status"] == "proposed"

    early_register = client.post("/api/capabilities/hello-tool/0.1.0/register")
    assert early_register.status_code == 409

    started = client.post("/api/capabilities/hello-tool/0.1.0/validate/start")
    assert started.status_code == 200
    report = client.post(
        "/api/capabilities/hello-tool/0.1.0/validate",
        json={
            "static_checks": True,
            "unit_tests": True,
            "security_checks": True,
            "regression_tests": True,
        },
    )
    assert report.status_code == 200
    assert report.json()["status"] == "validated"

    registered = client.post("/api/capabilities/hello-tool/0.1.0/register")
    assert registered.status_code == 200
    assert registered.json()["status"] == "registered"

    activation = client.post("/api/capabilities/hello-tool/0.1.0/activate")
    assert activation.status_code == 409
    assert "sandbox" in activation.json()["detail"].lower()


def test_failed_capability_validation_cannot_be_registered(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
        )
    )
    created = client.post(
        "/api/capabilities",
        json={
            "capability_id": "unsafe-tool",
            "version": "0.1.0",
            "name": "Unsafe tool",
            "source_code": "print('untrusted')",
            "rationale": "Exercise failed validation path.",
        },
    )
    assert created.status_code == 201
    client.post("/api/capabilities/unsafe-tool/0.1.0/validate/start")
    failed = client.post(
        "/api/capabilities/unsafe-tool/0.1.0/validate",
        json={
            "static_checks": True,
            "unit_tests": True,
            "security_checks": False,
            "regression_tests": True,
        },
    )
    assert failed.status_code == 200
    assert failed.json()["status"] == "rejected"
    assert client.post("/api/capabilities/unsafe-tool/0.1.0/register").status_code == 409
