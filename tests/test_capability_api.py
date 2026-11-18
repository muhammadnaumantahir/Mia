import hashlib

from fastapi.testclient import TestClient

from mia.evolution.capabilities import CapabilityStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeSandboxRunner:
    def __init__(self, *, passing=True, available=True):
        self.passing = passing
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def validate_candidate(
        self,
        *,
        source_code,
        test_source_code,
        source_sha256=None,
        test_sha256=None,
    ):
        self.calls.append({
            "source_code": source_code,
            "test_source_code": test_source_code,
            "source_sha256": source_sha256,
            "test_sha256": test_sha256,
        })
        return {
            "static_checks": True,
            "unit_tests": True,
            "security_checks": self.passing,
            "regression_tests": True,
            "evidence": {
                "runner": "docker-isolated-v1",
                "image_id": "sha256:test-image",
                "source_sha256": hashlib.sha256(source_code.encode("utf-8")).hexdigest(),
                "test_sha256": hashlib.sha256(test_source_code.encode("utf-8")).hexdigest(),
            },
        }


def _create_candidate(client, capability_id, source, tests):
    return client.post(
        "/api/capabilities",
        json={
            "capability_id": capability_id,
            "version": "0.1.0",
            "name": capability_id,
            "source_code": source,
            "test_source_code": tests,
            "rationale": "Exercise the Docker-backed validation API.",
            "source_kind": "generated",
            "permissions": [],
        },
    )


def test_capability_api_requires_all_sandbox_gates_before_registration(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
        )
    )
    source = "def hello():\n    return 'hello'\n"
    tests = "from candidate import hello\n\ndef test_hello():\n    assert hello() == 'hello'\n"
    created = _create_candidate(client, "hello-tool", source, tests)
    assert created.status_code == 201
    assert created.json()["status"] == "proposed"
    assert created.json()["test_sha256"]

    early_register = client.post("/api/capabilities/hello-tool/0.1.0/register")
    assert early_register.status_code == 409

    started = client.post("/api/capabilities/hello-tool/0.1.0/validate/start")
    assert started.status_code == 200
    assert started.json()["status"] == "validating"

    # Client-supplied claims are ignored; the server invokes the injected runner.
    report = client.post(
        "/api/capabilities/hello-tool/0.1.0/validate",
        json={
            "static_checks": False,
            "unit_tests": False,
            "security_checks": False,
            "regression_tests": False,
        },
    )
    assert report.status_code == 200
    assert report.json()["status"] == "validated"
    assert len(runner.calls) == 1
    assert runner.calls[0]["source_code"] == source
    assert runner.calls[0]["test_source_code"] == tests

    registered = client.post("/api/capabilities/hello-tool/0.1.0/register")
    assert registered.status_code == 200
    assert registered.json()["status"] == "registered"

    activation = client.post("/api/capabilities/hello-tool/0.1.0/activate")
    assert activation.status_code == 409
    assert "sandbox" in activation.json()["detail"].lower()


def test_failed_sandbox_validation_cannot_be_registered(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner(passing=False)
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
        )
    )
    source = "print('untrusted')\n"
    tests = "def test_candidate_source_exists():\n    assert True\n"
    created = _create_candidate(client, "unsafe-tool", source, tests)
    assert created.status_code == 201
    assert client.post("/api/capabilities/unsafe-tool/0.1.0/validate/start").status_code == 200
    failed = client.post("/api/capabilities/unsafe-tool/0.1.0/validate")
    assert failed.status_code == 200
    assert failed.json()["status"] == "rejected"
    assert client.post("/api/capabilities/unsafe-tool/0.1.0/register").status_code == 409


def test_sandbox_unavailable_does_not_start_validation(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=FakeSandboxRunner(available=False),
        )
    )
    created = _create_candidate(
        client,
        "unavailable",
        "def value(): return 1\n",
        "def test_value(): assert True\n",
    )
    assert created.status_code == 201
    response = client.post("/api/capabilities/unavailable/0.1.0/validate/start")
    assert response.status_code == 503
    assert store.get("unavailable", "0.1.0")["status"] == "proposed"


def test_capability_validation_requires_test_source_before_sandbox(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
        )
    )
    created = client.post(
        "/api/capabilities",
        json={
            "capability_id": "missing-tests",
            "version": "0.1.0",
            "name": "Missing tests",
            "source_code": "def value(): return 1\n",
            "rationale": "Test mandatory candidate tests.",
        },
    )
    assert created.status_code == 201
    response = client.post("/api/capabilities/missing-tests/0.1.0/validate/start")
    assert response.status_code == 422
    assert runner.calls == []
    assert store.get("missing-tests", "0.1.0")["status"] == "proposed"


def test_capability_source_api_returns_hash_verified_immutable_source(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
        )
    )
    source = "def add(a, b):\n    return a + b\n"
    tests = "from candidate import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    created = _create_candidate(client, "math-check", source, tests)
    assert created.status_code == 201

    response = client.get("/api/capabilities/math-check/0.1.0/source")
    assert response.status_code == 200
    assert response.json()["source_code"] == source
    assert response.json()["test_source_code"] == tests
    assert response.json()["source_sha256"] == created.json()["source_sha256"]
    assert response.json()["test_sha256"] == created.json()["test_sha256"]


def test_capability_source_api_rejects_tampered_source(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
        )
    )
    created = _create_candidate(
        client,
        "tampered-source",
        "def value(): return 1\n",
        "from candidate import value\ndef test_value(): assert value() == 1\n",
    )
    assert created.status_code == 201
    path = store.get("tampered-source", "0.1.0")["source_path"]
    with open(path, "w", encoding="utf-8") as source_file:
        source_file.write("def value(): return 99\n")

    response = client.get("/api/capabilities/tampered-source/0.1.0/source")
    assert response.status_code == 409
    assert "hash does not match" in response.json()["detail"]
