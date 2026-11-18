import pytest

from mia.evolution.capabilities import (
    CapabilityActivationBlocked,
    CapabilityLifecycleError,
    CapabilityStore,
)


def passing_report():
    return {
        "static_checks": True,
        "unit_tests": True,
        "security_checks": True,
        "regression_tests": True,
    }


def test_capability_candidate_is_persisted_and_registered_without_execution(tmp_path):
    store = CapabilityStore(
        tmp_path / "capabilities.sqlite3",
        tmp_path / "generated",
    )
    source = "def hello():\n    return 'hello'\n"
    candidate = store.record_candidate(
        capability_id="hello-tool",
        version="0.1.0",
        name="Hello tool",
        source_code=source,
        rationale="Initial capability lifecycle test.",
    )
    assert candidate["status"] == "proposed"
    assert store.read_source("hello-tool", "0.1.0") == source

    store.begin_validation("hello-tool", "0.1.0")
    validated = store.record_validation(
        "hello-tool",
        "0.1.0",
        passing_report(),
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
    store.record_candidate(
        capability_id="risky-tool",
        version="0.1.0",
        name="Risky tool",
        source_code="print('candidate')",
        rationale="Check rejection path.",
    )
    store.begin_validation("risky-tool", "0.1.0")
    report = passing_report()
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
    store.record_candidate(
        capability_id="report-tool",
        version="1.0.0",
        name="Report tool",
        source_code="def run(): return 1",
        rationale="Initial version.",
    )
    store.begin_validation("report-tool", "1.0.0")
    store.record_validation("report-tool", "1.0.0", passing_report())
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
