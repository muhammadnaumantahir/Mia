"""Evidence-backed workload discovery that creates proposals, never approvals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .tasks import TASK_SOURCES, TaskStore


@dataclass(frozen=True, slots=True)
class DiscoveryFinding:
    source: str
    evidence_id: str
    title: str
    goal: str
    evidence_summary: str
    risk_level: str = "low"
    priority: int = 50
    evidence_uri: str | None = None


class WorkloadDiscovery:
    """Convert observed findings into deduplicated, owner-reviewable task proposals."""

    VALID_RISKS = {"low", "medium", "high"}

    def __init__(self, tasks: TaskStore) -> None:
        self.tasks = tasks

    def propose(
        self,
        findings: list[DiscoveryFinding],
        *,
        actor: str = "workload-discovery",
    ) -> list[dict[str, Any]]:
        if len(findings) > 50:
            raise ValueError("at most 50 findings may be processed in one batch")
        proposals: list[dict[str, Any]] = []

        for finding in findings:
            if finding.source not in TASK_SOURCES - {"interactive"}:
                raise ValueError(f"unsupported discovery source: {finding.source}")
            if finding.risk_level not in self.VALID_RISKS:
                raise ValueError("risk_level must be low, medium or high")
            if not finding.evidence_id.strip() or len(finding.evidence_id) > 300:
                raise ValueError("evidence_id must be 1-300 characters")
            if not finding.evidence_summary.strip() or len(finding.evidence_summary) > 2000:
                raise ValueError("evidence_summary must be 1-2000 characters")

            identity = {
                "source": finding.source,
                "evidence_id": finding.evidence_id,
                "title": finding.title.strip(),
            }
            dedupe_key = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            metadata = {
                "discovery": True,
                "evidence_id": finding.evidence_id,
                "evidence_summary": finding.evidence_summary,
                "evidence_uri": finding.evidence_uri,
                "risk_level": finding.risk_level,
                "requires_governance_review": True,
                "auto_approval_eligible": False,
            }
            task_id = self.tasks.create_task(
                title=finding.title,
                goal=finding.goal,
                source=finding.source,
                priority=finding.priority,
                metadata=metadata,
                actor=actor,
                dedupe_key=dedupe_key,
            )
            proposals.append({
                "task_id": task_id,
                "dedupe_key": dedupe_key,
                "status": "proposed",
                "risk_level": finding.risk_level,
                "requires_governance_review": True,
            })

        return proposals
