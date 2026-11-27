import pytest

from mia.evolution.capabilities import (
    CapabilityActivationBlocked,
    CapabilityLifecycleError,
    CapabilityStore,
)


def passing_report(candidate):
    return {
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
    }


def test_capability_candidate_is_persisted_and_registered_without_execution(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    source = "def hello():\n    return 'hello'\n"
    test_source = "from candidate import hello\n\ndef test_hello():\n    assert hello() == 'hello'\n"
    candidate = store.record_candidate(
        capability_id="hello-tool",
        version="0.1.0",
        name="Hello tool",
        source_code=source,
        test_source_code=test_source,
        rationale="Initial capability lifecycle test.",
    )
    assert candidate["status"] == "proposed"
    assert store.read_source("hello-tool", "0.1.0") == source
    assert store.read_test_source("hello-tool", "0.1.0") == test_source

    store.begin_validation("hello-tool", "0.1.0")
    validated = store.record_validation(
        "hello-tool",
        "0.1.0",
        passing_report(candidate),
    )
    assert validated["status"] == "validated"

    registered = store.register("hello-tool", "0.1.0")
    assert registered["status"] == "registered"

    with pytest.raises(CapabilityActivationBlocked):
        store.activate("hello-tool", "0.1.0")


def test_failed_validation_blocks_registration(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    candidate = store.record_candidate(
        capability_id="risky-tool",
        version="0.1.0",
        name="Risky tool",
        source_code="print('candidate')",
        test_source_code="def test_candidate_source_exists():\n    assert True\n",
        rationale="Check rejection path.",
    )
    store.begin_validation("risky-tool", "0.1.0")
    report = passing_report(candidate)
    report["security_checks"] = False
    rejected = store.record_validation("risky-tool", "0.1.0", report)
    assert rejected["status"] == "rejected"

    with pytest.raises(CapabilityLifecycleError):
        store.register("risky-tool", "0.1.0")


def test_capability_lineage_is_versioned_and_immutable(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    candidate = store.record_candidate(
        capability_id="report-tool",
        version="1.0.0",
        name="Report tool",
        source_code="def run(): return 1",
        test_source_code="from candidate import run\n\ndef test_run(): assert run() == 1\n",
        rationale="Initial version.",
    )
    store.begin_validation("report-tool", "1.0.0")
    store.record_validation("report-tool", "1.0.0", passing_report(candidate))
    store.register("report-tool", "1.0.0")

    child = store.record_candidate(
        capability_id="report-tool",
        version="1.1.0",
        name="Report tool",
        source_code="def run(): return 2",
        rationale="Improve output.",
        parent_id="report-tool",
        parent_version="1.0.0",
    )
    assert child["parent_version"] == "1.0.0"
    assert [item["version"] for item in store.lineage("report-tool", "1.1.0")] == [
        "1.1.0",
        "1.0.0",
    ]
    with pytest.raises(FileExistsError):
        store.record_candidate(
            capability_id="report-tool",
            version="1.1.0",
            name="Report tool",
            source_code="def run(): return 99",
            rationale="Attempt to replace version.",
            parent_id="report-tool",
            parent_version="1.0.0",
        )


def _register_dependency(store, capability_id="base-helper"):
    candidate = store.record_candidate(
        capability_id=capability_id,
        version="1.0.0",
        name=capability_id,
        source_code="def run(input_data): return {'ok': True}\n",
        test_source_code="from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n",
        rationale="Registered dependency fixture.",
    )
    store.begin_validation(capability_id, "1.0.0")
    store.record_validation(capability_id, "1.0.0", passing_report(candidate))
    return store.register(capability_id, "1.0.0")


def test_capability_dependencies_are_exact_versioned_and_immutable(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    _register_dependency(store)
    candidate = store.record_candidate(
        capability_id="composed-tool",
        version="0.1.0",
        name="Composed tool",
        source_code="def run(input_data): return {'ok': True}\n",
        test_source_code="from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n",
        rationale="Depends on an already registered helper.",
        dependencies=[{"capability_id": "base-helper", "version": "1.0.0"}],
    )
    assert candidate["dependencies"] == [
        {"capability_id": "base-helper", "version": "1.0.0"}
    ]
    with pytest.raises(ValueError, match="existing registered"):
        store.record_candidate(
            capability_id="missing-dep",
            version="0.1.0",
            name="Missing dependency",
            source_code="def run(input_data): return {}\n",
            rationale="Unknown dependency must fail closed.",
            dependencies=[{"capability_id": "does-not-exist", "version": "1.0.0"}],
        )
    with pytest.raises(ValueError, match="cannot depend on itself"):
        store.record_candidate(
            capability_id="self-dep",
            version="0.1.0",
            name="Self dependency",
            source_code="def run(input_data): return {}\n",
            rationale="Self dependency must fail closed.",
            dependencies=[{"capability_id": "self-dep", "version": "0.1.0"}],
        )


def test_retired_capability_dependency_blocks_registration(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    _register_dependency(store, "retirable-helper")
    candidate = store.record_candidate(
        capability_id="dependent-tool",
        version="0.1.0",
        name="Dependent tool",
        source_code="def run(input_data): return {'ok': True}\n",
        test_source_code="from candidate import run\ndef test_run(): assert run({}) == {'ok': True}\n",
        rationale="Dependency availability is checked again at registration.",
        dependencies=[{"capability_id": "retirable-helper", "version": "1.0.0"}],
    )
    store.begin_validation("dependent-tool", "0.1.0")
    store.record_validation("dependent-tool", "0.1.0", passing_report(candidate))
    store.retire("retirable-helper", "1.0.0", "Owner retired dependency")
    with pytest.raises(CapabilityLifecycleError, match="dependency"):
        store.register("dependent-tool", "0.1.0")


def test_validation_rejects_unbound_or_forged_sandbox_evidence(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    candidate = store.record_candidate(
        capability_id="bound-tool",
        version="0.1.0",
        name="Bound tool",
        source_code="def value(): return 7\n",
        test_source_code="from candidate import value\n\ndef test_value(): assert value() == 7\n",
        rationale="Verify candidate and test hashes are required.",
    )
    store.begin_validation("bound-tool", "0.1.0")

    with pytest.raises(ValueError, match="sandbox evidence"):
        store.record_validation(
            "bound-tool",
            "0.1.0",
            {
                "static_checks": True,
                "unit_tests": True,
                "security_checks": True,
                "regression_tests": True,
            },
        )

    report = passing_report(candidate)
    report["evidence"]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash"):
        store.record_validation("bound-tool", "0.1.0", report)


def test_validation_cannot_pass_unit_tests_without_test_source(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    candidate = store.record_candidate(
        capability_id="no-tests",
        version="0.1.0",
        name="No tests",
        source_code="def value(): return 7\n",
        rationale="Unit test pass must require stored test source.",
    )
    store.begin_validation("no-tests", "0.1.0")
    report = passing_report(candidate)
    report["unit_tests"] = True

    with pytest.raises(ValueError, match="test source"):
        store.record_validation("no-tests", "0.1.0", report)


def test_capability_store_migrates_older_schema(tmp_path):
    database = tmp_path / "old.sqlite3"
    import sqlite3

    with sqlite3.connect(database) as db:
        db.execute("""
            CREATE TABLE capability_versions (
                capability_id TEXT NOT NULL,
                version TEXT NOT NULL,
                name TEXT NOT NULL,
                parent_id TEXT,
                parent_version TEXT,
                rationale TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                upstream_uri TEXT,
                license_id TEXT,
                permissions_json TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                source_path TEXT NOT NULL,
                status TEXT NOT NULL,
                validation_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (capability_id, version)
            )
        """)

    store = CapabilityStore(database, tmp_path / "generated")
    candidate = store.record_candidate(
        capability_id="migrated",
        version="0.1.0",
        name="Migrated",
        source_code="def value(): return 1\n",
        test_source_code="from candidate import value\n\ndef test_value(): assert value() == 1\n",
        rationale="Store works after schema migration.",
    )
    assert candidate["test_sha256"]
    assert candidate["dependencies"] == []
    assert store.read_test_source("migrated", "0.1.0").startswith("from candidate")
