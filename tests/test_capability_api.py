import hashlib

import pytest
from fastapi.testclient import TestClient

from mia.agent.resources import ResourceMonitor
from mia.computer.approvals import ApprovalStore
from mia.computer.capabilities import PermissionLevel
from mia.computer.filesystem import FilesystemAdapter
from mia.evolution.capabilities import CapabilityStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakePsutil:
    def cpu_percent(self, interval):
        return 10

    def virtual_memory(self):
        from types import SimpleNamespace
        return SimpleNamespace(percent=20, available=800, total=1000)

    def process_iter(self, attrs):
        return iter([])


class FakeSandboxRunner:
    def __init__(self, *, passing=True, available=True, execution_error=None, action_requests=None):
        self.passing = passing
        self.available = available
        self.execution_error = execution_error
        self.action_requests = action_requests
        self.calls = []
        self.execution_calls = []

    def is_available(self):
        return self.available

    def execute_capability(self, *, source_code, source_sha256, input_data):
        self.execution_calls.append({
            "source_code": source_code,
            "source_sha256": source_sha256,
            "input_data": input_data,
        })
        if self.execution_error is not None:
            raise self.execution_error
        result = {"echo": input_data.get("value", 0)}
        if self.action_requests is not None:
            result["_mia_actions"] = self.action_requests
        return {
            "result": result,
            "source_sha256": source_sha256,
            "image_id": "sha256:test-image",
            "runner": "docker-capability-execution-v1",
        }

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


def test_capability_api_persists_dependencies_and_refuses_retired_dependency(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    dependency = store.record_candidate(
        capability_id="base-helper",
        version="1.0.0",
        name="Base helper",
        source_code="def run(input_data): return {'base': True}\n",
        test_source_code="from candidate import run\ndef test_run(): assert run({}) == {'base': True}\n",
        rationale="Dependency fixture.",
    )
    store.begin_validation("base-helper", "1.0.0")
    store.record_validation("base-helper", "1.0.0", {
        "static_checks": True,
        "unit_tests": True,
        "security_checks": True,
        "regression_tests": True,
        "evidence": {
            "runner": "docker-isolated-v1",
            "image_id": "sha256:test-image",
            "source_sha256": dependency["source_sha256"],
            "test_sha256": dependency["test_sha256"],
        },
    })
    store.register("base-helper", "1.0.0")
    runner = FakeSandboxRunner()
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
    ))
    response = client.post("/api/capabilities", json={
        "capability_id": "composed-tool",
        "version": "0.1.0",
        "name": "Composed tool",
        "source_code": "def run(input_data): return {'ok': True}\n",
        "test_source_code": "from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n",
        "rationale": "Pin an exact registered dependency version.",
        "dependencies": [{"capability_id": "base-helper", "version": "1.0.0"}],
    })
    assert response.status_code == 201
    assert response.json()["dependencies"] == [
        {"capability_id": "base-helper", "version": "1.0.0"}
    ]
    store.begin_validation("composed-tool", "0.1.0")
    candidate = store.get("composed-tool", "0.1.0")
    store.record_validation("composed-tool", "0.1.0", {
        "static_checks": True,
        "unit_tests": True,
        "security_checks": True,
        "regression_tests": True,
        "evidence": {
            "runner": "docker-isolated-v1",
            "image_id": "sha256:test-image",
            "source_sha256": candidate["source_sha256"],
            "test_sha256": candidate["test_sha256"],
        },
    })
    store.register("composed-tool", "0.1.0")
    store.retire("base-helper", "1.0.0", "Owner retired dependency")
    execution = client.post(
        "/api/capabilities/composed-tool/0.1.0/execute",
        json={"input_data": {}},
    )
    assert execution.status_code == 409
    assert "dependency" in execution.json()["detail"].lower()
    assert runner.execution_calls == []


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


def test_capability_source_api_allows_legacy_source_without_tests(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
        )
    )
    source = "def value(): return 3\n"
    created = client.post(
        "/api/capabilities",
        json={
            "capability_id": "legacy-source",
            "version": "0.1.0",
            "name": "Legacy source",
            "source_code": source,
            "rationale": "Verify source inspection for older candidates without test files.",
        },
    )
    assert created.status_code == 201

    response = client.get("/api/capabilities/legacy-source/0.1.0/source")
    assert response.status_code == 200
    assert response.json()["source_code"] == source
    assert response.json()["test_source_code"] is None


def test_registered_permission_free_capability_executes_through_isolated_runner(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
            resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        )
    )
    source = (
        "def run(input_data):\n"
        "    return {'echo': input_data.get('value', 0)}\n"
    )
    tests = (
        "from candidate import run\n"
        "def test_run():\n"
        "    assert run({'value': 3}) == {'echo': 3}\n"
    )
    created = _create_candidate(client, "echo-capability", source, tests)
    assert created.status_code == 201
    assert client.post(
        "/api/capabilities/echo-capability/0.1.0/validate/start"
    ).status_code == 200
    validated = client.post("/api/capabilities/echo-capability/0.1.0/validate")
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"
    registered = client.post("/api/capabilities/echo-capability/0.1.0/register")
    assert registered.status_code == 200

    executed = client.post(
        "/api/capabilities/echo-capability/0.1.0/execute",
        json={"input_data": {"value": 37}},
    )

    assert executed.status_code == 200, executed.text
    body = executed.json()
    assert body["result"] == {"echo": 37}
    assert body["activated"] is False
    assert body["runner"] == "docker-capability-execution-v1"
    assert runner.execution_calls == [{
        "source_code": source,
        "source_sha256": created.json()["source_sha256"],
        "input_data": {"value": 37},
    }]
    assert any(
        event["event_type"] == "capability_executed"
        for event in store_events(client)
    )


def test_permissioned_capability_brokers_action_into_owner_reviewed_task(tmp_path):
    root = tmp_path / "workspace-root"
    root.mkdir()
    approvals = ApprovalStore(tmp_path / "computer-approvals.sqlite3")
    filesystem = FilesystemAdapter([root], approvals)
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3", tmp_path / "generated", activation_enabled=True
    )
    runner = FakeSandboxRunner(
        action_requests=[{
            "capability": "filesystem.write",
            "args": {
                "root_id": "root_1",
                "relative_path": "notes/result.txt",
                "content": "created by a governed capability request",
            },
            "description": "Save the generated report",
        }]
    )
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        filesystem=filesystem,
        approvals=approvals,
    ))
    grant = client.app.state.permission_policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(root),
        kind="session",
    )

    source = (
        "def run(input_data):\n"
        "    return {'summary': input_data.get('summary', 'done')}\n"
    )
    tests = "from candidate import run\ndef test_run(): assert run({'summary': 'ok'}) == {'summary': 'ok'}\n"
    created = client.post("/api/capabilities", json={
        "capability_id": "report-writer",
        "version": "0.1.0",
        "name": "Report writer",
        "source_code": source,
        "test_source_code": tests,
        "rationale": "Create a report and request its storage through Mia.",
        "permissions": ["filesystem.write"],
    })
    assert created.status_code == 201, created.text
    assert client.post("/api/capabilities/report-writer/0.1.0/validate/start").status_code == 200
    assert client.post("/api/capabilities/report-writer/0.1.0/validate").json()["status"] == "validated"
    assert client.post("/api/capabilities/report-writer/0.1.0/register").json()["status"] == "registered"
    activated = client.post(
        "/api/capabilities/report-writer/0.1.0/activate",
        json={"approved_by": "owner", "reason": "approved report writer scope"},
    )
    assert activated.status_code == 200, activated.text

    executed = client.post(
        "/api/capabilities/report-writer/0.1.0/execute",
        json={"input_data": {"summary": "prepared"}},
    )
    assert executed.status_code == 200, executed.text
    payload = executed.json()
    assert payload["activated"] is True
    assert payload["actions_executed"] is False
    assert payload["execution_policy"] == "docker-pure-data-with-owner-reviewed-task-requests"
    assert payload["result"] == {"echo": 0}
    assert len(payload["action_tasks"]) == 1
    action_task = payload["action_tasks"][0]
    assert action_task["status"] == "planned_owner_review"
    assert action_task["plan"][0]["capability"] == "filesystem.write"
    assert action_task["plan"][0]["needs_approval"] is False
    assert not (root / "notes" / "result.txt").exists()

    run = client.post(f"/api/computer/tasks/{action_task['task_id']}/run")
    assert run.status_code == 200, run.text
    assert run.json()["status"] == "completed"
    assert (root / "notes" / "result.txt").read_text(encoding="utf-8") == (
        "created by a governed capability request"
    )
    client.app.state.permission_policy.revoke(grant["grant_id"])


def test_permissioned_capability_cannot_execute_after_owner_grant_is_revoked(tmp_path):
    store = CapabilityStore(
        tmp_path / "revoked.sqlite3", tmp_path / "revoked-generated", activation_enabled=True
    )
    runner = FakeSandboxRunner()
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "revoked-observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    ))
    grant = client.app.state.permission_policy.grant(
        granted_by="owner",
        capabilities=["filesystem.write"],
        level=PermissionLevel.WRITE,
        directory=str(tmp_path),
        kind="session",
    )
    source = "def run(input_data):\n    return {'ok': True}\n"
    tests = "from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n"
    created = client.post("/api/capabilities", json={
        "capability_id": "revocable-capability",
        "version": "0.1.0",
        "name": "Revocable capability",
        "source_code": source,
        "test_source_code": tests,
        "rationale": "Verify grant revocation is immediate.",
        "permissions": ["filesystem.write"],
    })
    assert created.status_code == 201
    client.post("/api/capabilities/revocable-capability/0.1.0/validate/start")
    client.post("/api/capabilities/revocable-capability/0.1.0/validate")
    client.post("/api/capabilities/revocable-capability/0.1.0/register")
    activated = client.post(
        "/api/capabilities/revocable-capability/0.1.0/activate",
        json={"approved_by": "owner", "reason": "approved"},
    )
    assert activated.status_code == 200
    client.app.state.permission_policy.revoke(grant["grant_id"])

    response = client.post(
        "/api/capabilities/revocable-capability/0.1.0/execute",
        json={"input_data": {}},
    )
    assert response.status_code == 403
    assert runner.execution_calls == []

def store_events(client):
    response = client.get("/api/observatory/events?limit=50")
    assert response.status_code == 200
    return response.json()["items"]


def test_capability_execution_requires_registration_and_empty_permissions(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
        )
    )
    source = "def run(input_data):\n    return {'ok': True}\n"
    tests = (
        "from candidate import run\n"
        "def test_run():\n"
        "    assert run({}) == {'ok': True}\n"
    )
    created = client.post(
        "/api/capabilities",
        json={
            "capability_id": "scoped-capability",
            "version": "0.1.0",
            "name": "Scoped capability",
            "source_code": source,
            "test_source_code": tests,
            "rationale": "Verify permission gating.",
            "permissions": ["filesystem.read"],
        },
    )
    assert created.status_code == 201
    early = client.post(
        "/api/capabilities/scoped-capability/0.1.0/execute",
        json={"input_data": {}},
    )
    assert early.status_code == 409
    client.post("/api/capabilities/scoped-capability/0.1.0/validate/start")
    client.post("/api/capabilities/scoped-capability/0.1.0/validate")
    client.post("/api/capabilities/scoped-capability/0.1.0/register")

    blocked = client.post(
        "/api/capabilities/scoped-capability/0.1.0/execute",
        json={"input_data": {}},
    )
    assert blocked.status_code == 409
    assert runner.execution_calls == []


def test_capability_execution_respects_emergency_stop_and_resource_pressure(
    tmp_path,
    monkeypatch,
):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    monkeypatch.setenv("MIA_EMERGENCY_STOP", "true")
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            capability_store=store,
            sandbox_runner=runner,
            resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
        )
    )
    source = "def run(input_data):\n    return {'ok': True}\n"
    tests = "from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n"
    created = _create_candidate(client, "stop-capability", source, tests)
    assert created.status_code == 201
    client.post("/api/capabilities/stop-capability/0.1.0/validate/start")
    client.post("/api/capabilities/stop-capability/0.1.0/validate")
    client.post("/api/capabilities/stop-capability/0.1.0/register")

    stopped = client.post(
        "/api/capabilities/stop-capability/0.1.0/execute",
        json={"input_data": {}},
    )
    assert stopped.status_code == 503
    assert stopped.json()["detail"] == "Emergency stop is active"
    assert runner.execution_calls == []

    monkeypatch.setenv("MIA_EMERGENCY_STOP", "false")

    class HotPsutil(FakePsutil):
        def cpu_percent(self, interval):
            return 95

    hot_client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "hot-observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=HotPsutil()),
    ))
    with hot_client:
        hot = hot_client.post(
            "/api/capabilities/stop-capability/0.1.0/execute",
            json={"input_data": {}},
        )
    assert hot.status_code == 503
    assert "CPU or memory" in hot.json()["detail"]
    assert runner.execution_calls == []


def _register_permission_free_capability(client, capability_id="boundary-capability"):
    source = "def run(input_data):\n    return {'echo': input_data.get('value', 0)}\n"
    tests = (
        "from candidate import run\n"
        "def test_run():\n"
        "    assert run({'value': 3}) == {'echo': 3}\n"
    )
    created = _create_candidate(client, capability_id, source, tests)
    assert created.status_code == 201
    started = client.post(
        f"/api/capabilities/{capability_id}/0.1.0/validate/start"
    )
    assert started.status_code == 200
    validated = client.post(f"/api/capabilities/{capability_id}/0.1.0/validate")
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"
    registered = client.post(f"/api/capabilities/{capability_id}/0.1.0/register")
    assert registered.status_code == 200
    return source, created.json()["source_sha256"]


def test_capability_execution_rejects_oversized_input_before_runner(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    ))
    _register_permission_free_capability(client, "big-input-capability")

    response = client.post(
        "/api/capabilities/big-input-capability/0.1.0/execute",
        json={"input_data": {"value": "x" * (33 * 1024)}},
    )

    assert response.status_code == 422
    assert "32 KB" in response.json()["detail"]
    assert runner.execution_calls == []


def test_capability_execution_rejects_tampered_registered_source(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    runner = FakeSandboxRunner()
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        capability_store=store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    ))
    _register_permission_free_capability(client, "tampered-execution-capability")
    candidate = store.get("tampered-execution-capability", "0.1.0")
    with open(candidate["source_path"], "w", encoding="utf-8") as source_file:
        source_file.write("def run(input_data): return {'tampered': True}\n")

    response = client.post(
        "/api/capabilities/tampered-execution-capability/0.1.0/execute",
        json={"input_data": {}},
    )

    assert response.status_code == 409
    assert "hash does not match" in response.json()["detail"]
    assert runner.execution_calls == []


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_outcome"),
    [
        ("unavailable", 503, "unavailable"),
        ("execution", 502, "failed"),
    ],
)
def test_capability_execution_audits_docker_failures_without_raw_payload(
    tmp_path,
    error,
    expected_status,
    expected_outcome,
):
    from mia.evolution.sandbox import SandboxExecutionError, SandboxUnavailableError

    failure = (
        SandboxUnavailableError("Docker image missing")
        if error == "unavailable"
        else SandboxExecutionError("container returned an invalid execution result")
    )
    observatory = ObservatoryStore(tmp_path / f"{error}-observatory.sqlite3")
    capability_store = CapabilityStore(
        tmp_path / f"{error}-capabilities.sqlite3",
        tmp_path / f"{error}-generated",
    )
    runner = FakeSandboxRunner(execution_error=failure)
    client = TestClient(create_app(
        store=observatory,
        capability_store=capability_store,
        sandbox_runner=runner,
        resource_monitor=ResourceMonitor(psutil_module=FakePsutil()),
    ))
    private_value = "private-user-input-should-not-be-logged"
    _register_permission_free_capability(client, f"{error}-capability")

    response = client.post(
        f"/api/capabilities/{error}-capability/0.1.0/execute",
        json={"input_data": {"value": private_value}},
    )

    assert response.status_code == expected_status
    failed = next(
        event for event in observatory.list_events(limit=50)
        if event["event_type"] == "capability_execution_failed"
    )
    assert failed["outcome"] == expected_outcome
    assert private_value not in failed["payload_json"]
    assert "input_sha256" in failed["payload_json"]
