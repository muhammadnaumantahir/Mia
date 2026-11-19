"""Result verification, completion-claim checks and recovery advice (SPS-09 / SPS-06).

A successful-sounding model reply is NOT evidence. This module keeps an operation
ledger with explicit states - planned, attempted, completed, failed, verified - and
checks assistant text against it. Model-based judging is deliberately absent from the
default path (no repeated LLM calls); any such signal would be advisory only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .broker import ToolCallRecord
from .intent import IntentAnalysis

OP_STATES = ("planned", "attempted", "completed", "failed", "verified")

_MUTATING_CLAIM = re.compile(
    r"\bI(?:'ve| have|'ve now| have now)?\s+(?:just\s+|now\s+|successfully\s+)?"
    r"(created|deleted|removed|sent|emailed|launched|opened|saved|wrote|written|installed|updated|"
    r"ran|executed|fixed|applied|moved|renamed|uploaded|modified|changed|deployed|published)\b",
    re.I)
_MUTATING_CLAIM_B = re.compile(
    r"\b(?:the|your)\s+(?:file|folder|app|application|task|change|changes|email|message)\b[^.\n]{0,40}"
    r"\b(?:has|have) been\s+(created|deleted|removed|sent|launched|opened|saved|written|installed|"
    r"updated|moved|renamed|uploaded|modified|changed|deployed|published|completed)\b", re.I)
_READ_CLAIM = re.compile(
    r"\bI(?:'ve| have)?\s+(?:just\s+|now\s+)?(inspected|read|listed|searched|checked|scanned|"
    r"analy[sz]ed the (?:files|project|repo|repository|code)|looked (?:at|through)|reviewed the (?:files|project|code))\b",
    re.I)
_HEDGES = re.compile(
    r"\b(i think|i believe|probably|might be|not sure|i'm unsure|i am unsure|possibly|may be|"
    r"i can't verify|cannot verify|as far as i know)\b", re.I)
_MEMORY_CITE = re.compile(r"\[M(\d+)\]")


@dataclass(slots=True)
class Operation:
    id: str
    description: str
    status: str = "planned"
    kind: str = "tool"  # tool | plan_step | response
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "description": self.description, "status": self.status,
                "kind": self.kind, "detail": self.detail[:200]}


@dataclass(slots=True)
class OperationLedger:
    operations: list[Operation] = field(default_factory=list)

    def add(self, description: str, *, kind: str = "tool", status: str = "planned") -> Operation:
        op = Operation(f"op{len(self.operations) + 1}", description, status, kind)
        self.operations.append(op)
        return op

    def set(self, op: Operation, status: str, detail: str = "") -> None:
        if status not in OP_STATES:
            raise ValueError(f"invalid operation state: {status}")
        op.status, op.detail = status, detail

    def by_status(self, *statuses: str) -> list[Operation]:
        return [o for o in self.operations if o.status in statuses]

    @property
    def any_executed(self) -> bool:
        return bool(self.by_status("completed", "verified"))

    def to_list(self) -> list[dict[str, str]]:
        return [o.to_dict() for o in self.operations]


Verifier = Callable[[ToolCallRecord], str | None]  # returns a problem description or None


def _generic_tool_verifier(record: ToolCallRecord) -> str | None:
    out = record.output
    if not isinstance(out, dict) or not out:
        return "tool returned no data"
    if out.get("ok") is False or out.get("error"):
        return "tool output reports an error"
    try:
        json.dumps(out, default=str)
    except (TypeError, ValueError):
        return "tool output is not valid data"
    payload_keys = [k for k in out if k not in {"ok", "root_id", "truncated"}]
    if not payload_keys and out.get("ok") is not True:
        return "tool output carries no result"
    return None


def verify_tool_record(record: ToolCallRecord,
                       custom: dict[str, Verifier] | None = None) -> tuple[bool, str]:
    """Independently check a tool result. Marks ``record.verified`` only on success."""
    if record.status == "approval_required":
        ok = bool(record.pending_action and record.pending_action.get("approval_id"))
        record.verified = ok
        return ok, ("action prepared and awaiting owner approval; not yet executed" if ok
                    else "approval request is malformed")
    if record.status != "completed":
        record.verified = False
        return False, f"tool did not complete ({record.status})"
    verifier = (custom or {}).get(record.name, _generic_tool_verifier)
    problem = verifier(record)
    record.verified = problem is None
    return problem is None, problem or "result passed structural checks"


def validate_structured_output(text: str, required_keys: tuple[str, ...] = ()) -> str | None:
    """Return a problem description for invalid JSON output, else None."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"not valid JSON ({exc.msg})"
    if required_keys:
        if not isinstance(data, dict):
            return "expected a JSON object"
        missing = [k for k in required_keys if k not in data]
        if missing:
            return f"missing required field(s): {', '.join(missing)}"
    return None


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    detail: str
    severity: str = "warn"  # warn | error

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "severity": self.severity}


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    issues: tuple[Issue, ...]
    confidence: str  # high | medium | low
    annotated_text: str

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "confidence": self.confidence,
                "issues": [i.to_dict() for i in self.issues]}


UNVERIFIED_NOTE = (
    "\n\nNote: I have no record of actually performing the action(s) described above, "
    "so please treat that as not done. Nothing is changed until an action is approved and run."
)


def evaluate_response(
    text: str,
    *,
    analysis: IntentAnalysis,
    ledger: OperationLedger,
    memory_reference_count: int = 0,
    required_json_keys: tuple[str, ...] | None = None,
    structured: bool = False,
) -> EvaluationReport:
    """Cheap, deterministic quality checks. May annotate the text but never invents content."""
    issues: list[Issue] = []
    out = text
    stripped = text.strip()

    if not stripped:
        issues.append(Issue("empty_response", "the model produced no usable text", "error"))
    if analysis.needs_clarification and stripped and "?" not in stripped:
        issues.append(Issue("missing_information",
                            "the request was ambiguous but the reply did not ask for clarification"))
    if structured:
        problem = validate_structured_output(text, required_json_keys or ())
        if problem:
            issues.append(Issue("invalid_structured_output", problem, "error"))

    executed = ledger.any_executed
    claimed_mutation = bool(_MUTATING_CLAIM.search(text) or _MUTATING_CLAIM_B.search(text))
    if claimed_mutation:
        # Registered tools are read/preview only, so a mutation claim is only
        # supportable by a verified, owner-approved execution record.
        has_exec = any(o.kind == "approved_execution" and o.status in {"completed", "verified"}
                       for o in ledger.operations)
        if not has_exec:
            issues.append(Issue("unverified_completion_claim",
                                "text claims an action was performed without an execution record"))
            out = out.rstrip() + UNVERIFIED_NOTE
    if _READ_CLAIM.search(text) and not executed:
        issues.append(Issue("unverified_inspection_claim",
                            "text claims to have inspected something but no tool completed"))
    failed = ledger.by_status("failed")
    if failed and not re.search(r"\b(fail|couldn't|could not|unable|error|didn't work|did not work|blocked)\b",
                                text, re.I):
        issues.append(Issue("failure_not_reported",
                            f"{len(failed)} operation(s) failed but the reply does not say so"))
        out = out.rstrip() + "\n\nNote: " + "; ".join(
            f"{o.description} failed" for o in failed[:3]) + "."
    cited = {int(n) for n in _MEMORY_CITE.findall(text)}
    if cited and (memory_reference_count == 0 or max(cited) > memory_reference_count):
        issues.append(Issue("unsupported_memory_citation",
                            "reply cites a memory item that was not provided"))
    hedges = len(_HEDGES.findall(text))
    if stripped and (hedges >= 3 or (len(stripped) < 25 and hedges >= 1)):
        issues.append(Issue("low_confidence", "reply is heavily hedged"))

    codes = {i.code for i in issues}
    if {"empty_response", "invalid_structured_output"} & codes:
        confidence = "low"
    elif codes & {"unverified_completion_claim", "unverified_inspection_claim", "low_confidence",
                  "failure_not_reported", "unsupported_memory_citation"}:
        confidence = "low" if len(codes) > 1 else "medium"
    elif codes:
        confidence = "medium"
    else:
        confidence = "high"
    return EvaluationReport(tuple(issues), confidence, out)


# ---------- recovery ----------

_RECOVERY = {
    "transient": ("a temporary problem (timeout or connection)",
                  "Try again in a moment; nothing was changed."),
    "permission": ("it is not permitted by the current permission policy",
                   "Grant the needed permission yourself if you want this, or choose a different approach."),
    "validation": ("the request details were not valid for that capability",
                   "Rephrase with the missing details (for example the exact path or name)."),
    "needs_approval": ("it needs your approval before anything runs",
                       "Review and approve the pending action, or reject it."),
    "needs_input": ("more information is needed", "Answer the question above so I can continue."),
    "permanent": ("it failed and retrying the same call will not help",
                  "Check the target exists and is accessible, or ask me to try another approach."),
}


def recovery_advice(error_class: str, what: str, error: str = "") -> str:
    reason, step = _RECOVERY.get(error_class or "permanent", _RECOVERY["permanent"])
    tail = f" ({error[:120]})" if error and error_class in {"permanent", "validation"} else ""
    return f"I could not complete '{what}' because {reason}{tail}. {step}"
