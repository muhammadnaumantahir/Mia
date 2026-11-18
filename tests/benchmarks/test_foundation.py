"""Fixed, local-only Mia baseline benchmarks; no network or external model calls."""

import pytest

from mia.agent.tasks import TaskStore, TaskTransitionError
from mia.agent.tools import ToolRegistry
from mia.evolution.capabilities import CapabilityStore
from mia.memory.store import MemoryStore
from mia.observatory.store import ObservatoryStore
from mia.sps.layers import SPS_LAYERS


def test_sps_framework_has_exactly_ten_ordered_layers():
    assert len(SPS_LAYERS) == 10
    assert [layer.number for layer in SPS_LAYERS] == list(range(1, 11))
    assert [layer.layer_id for layer in SPS_LAYERS] == [
        f"SPS-{number:02d}" for number in range(1, 11)
    ]


def test_task_governance_requires_approval_and_preserves_event_trace(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task_id = store.create_task(
        title="Baseline governance check",
        goal="Verify a proposed task cannot bypass owner approval.",
        source="benchmark",
    )

    with pytest.raises(TaskTransitionError):
        store.transition(
            task_id,
            "completed",
            reason="A proposed task must not complete directly.",
            actor="baseline-benchmark",
        )

    store.transition(
        task_id,
        "approved",
        reason="Baseline benchmark owner approval.",
        actor="baseline-benchmark",
    )
    claimed = store.claim_task(task_id, actor="baseline-benchmark")
    assert claimed["status"] == "running"
    assert [event["to_status"] for event in store.list_events(task_id)] == [
        "proposed",
        "approved",
        "running",
    ]


def test_read_only_tool_contract_rejects_preview_without_invoking_it():
    registry = ToolRegistry()
    invoked = []
    schema = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    registry.register(
        name="baseline.inspect",
        description="A read-only benchmark tool.",
        parameters=schema,
        permission="baseline.inspect",
        mode="read",
        handler=lambda arguments: invoked.append("read") or {"ok": True},
    )
    registry.register(
        name="baseline.preview",
        description="A preview-only benchmark tool.",
        parameters=schema,
        permission="baseline.preview",
        mode="preview",
        handler=lambda arguments: invoked.append("preview") or {"ok": True},
    )

    assert registry.validate_read_only_call("baseline.inspect", {}) is None
    assert "read-only" in registry.validate_read_only_call("baseline.preview", {})
    assert invoked == []


def test_memory_index_preserves_hash_and_untrusted_provenance(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    item_id = store.add_item(
        kind="research",
        title="Baseline evidence",
        content="Mia baseline evidence remains tagged as untrusted source material.",
        source_uri="https://example.invalid/baseline",
        trust_level="untrusted",
        tags=["baseline", "evidence"],
    )

    item = store.get_item(item_id)
    assert item is not None
    assert len(item["source_sha256"]) == 64
    assert item["trust_level"] == "untrusted"
    assert store.search("baseline evidence")


def test_capability_source_and_test_code_are_immutable_hashed_records(tmp_path):
    store = CapabilityStore(tmp_path / "capabilities.sqlite3", tmp_path / "generated")
    source = "def run(input_data):\n    return {'ok': True}\n"
    tests = "from candidate import run\n\ndef test_run():\n    assert run({}) == {'ok': True}\n"
    candidate = store.record_candidate(
        capability_id="baseline-capability",
        version="0.1.0",
        name="Baseline capability",
        source_code=source,
        test_source_code=tests,
        rationale="Verify immutable candidate evidence.",
    )

    assert candidate["status"] == "proposed"
    assert store.read_source("baseline-capability", "0.1.0") == source
    assert store.read_test_source("baseline-capability", "0.1.0") == tests
    assert len(candidate["source_sha256"]) == 64
    assert len(candidate["test_sha256"]) == 64


def test_observatory_records_actual_events_and_metrics(tmp_path):
    store = ObservatoryStore(tmp_path / "observatory.sqlite3")
    event_id = store.record_event(
        category="benchmark",
        event_type="baseline_check",
        summary="Fixed local benchmark suite executed.",
        outcome="success",
        layer_id="SPS-09",
    )
    sample_id = store.record_metric(
        day_index=0,
        metric_name="baseline_checks_passed",
        value=1,
        unit="checks",
        benchmark_id="mia-foundation-v1",
    )

    assert len(event_id) == 32
    assert len(sample_id) == 32
    assert store.summary()["event_count"] == 1
    assert store.summary()["metric_sample_count"] == 1
