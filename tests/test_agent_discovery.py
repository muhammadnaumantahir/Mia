from mia.agent.discovery import DiscoveryFinding, WorkloadDiscovery
from mia.agent.tasks import TaskStore


def test_workload_discovery_creates_deduplicated_proposals(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    discovery = WorkloadDiscovery(store)
    finding = DiscoveryFinding(
        source="benchmark",
        evidence_id="benchmark:v1:task-17:regression",
        title="Investigate task success regression",
        goal="Compare the last benchmark result to the immutable baseline.",
        evidence_summary="Task 17 changed from pass to fail in the fixed suite.",
        risk_level="medium",
        priority=80,
    )

    first = discovery.propose([finding])[0]
    second = discovery.propose([finding])[0]
    assert first["task_id"] == second["task_id"]
    assert first["status"] == "proposed"
    assert first["requires_governance_review"] is True
    assert store.get_task(first["task_id"])["status"] == "proposed"


def test_discovery_risk_level_never_auto_approves(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    discovery = WorkloadDiscovery(store)
    result = discovery.propose([
        DiscoveryFinding(
            source="self_assessment",
            evidence_id="layer:SPS-04:missing-parser",
            title="Investigate missing PDF parser",
            goal="Check whether an existing parser can be reused.",
            evidence_summary="The feature check could not find a registered PDF parser.",
            risk_level="high",
        )
    ])[0]
    task = store.get_task(result["task_id"])
    assert task["status"] == "proposed"
    assert task["metadata"]["requires_governance_review"] is True
    assert task["metadata"]["auto_approval_eligible"] is False
