"""Capability selection, permission evaluation and tool execution (SPS-02 / SPS-10).

The model only ever *proposes* a call. This broker keeps the stages separate:

1. capability selection - which registered tools are even offered for this turn;
2. proposal parsing      - untrusted model output -> (name, arguments);
3. permission evaluation - an evaluator the model cannot influence;
4. validation            - registered contract + argument schema;
5. execution             - bounded by a timeout, result interpreted and classified.

A tool is never granted because the model asks for it: a call is refused unless the
tool was offered this turn, is registered under that exact name, passes the
evaluator, and validates against its schema.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from mia.agent.tools import ToolDefinition, ToolRegistry, ToolResult

from .intent import IntentAnalysis
from .plan import Plan, PlanStep, StepOutcome

LOCAL_ONLY_TOOLS = frozenset({"filesystem.read_text", "memory.search_knowledge"})
_TRANSIENT_ERRORS = {"TimeoutError", "ConnectionError", "BlockingIOError", "ConnectionResetError"}


@dataclass(frozen=True, slots=True)
class GateDecision:
    allowed: bool
    stage: str  # parse | registered | offered | permission | egress | validation | ok
    reason: str = ""
    model_fault: bool = False  # the model produced a malformed/invented call


class PermissionEvaluator(Protocol):
    def evaluate(self, tool: ToolDefinition, arguments: dict[str, Any]) -> GateDecision: ...


class DefaultPermissionEvaluator:
    """Owner-configured policy. Only read and preview (approval-gated) modes are allowed."""

    def __init__(self, deny_permissions: Iterable[str] = (),
                 allowed_modes: Iterable[str] = ("read", "preview")) -> None:
        env = os.getenv("MIA_BRAIN_DENY_PERMISSIONS", "")
        self.deny = frozenset(deny_permissions) | frozenset(p.strip() for p in env.split(",") if p.strip())
        self.modes = frozenset(allowed_modes)

    def evaluate(self, tool: ToolDefinition, arguments: dict[str, Any]) -> GateDecision:
        if tool.mode not in self.modes:
            return GateDecision(False, "permission", f"mode '{tool.mode}' is not permitted")
        if tool.permission in self.deny:
            return GateDecision(False, "permission", f"permission '{tool.permission}' is denied by owner policy")
        return GateDecision(True, "ok")


@dataclass(slots=True)
class ToolCallRecord:
    name: str
    status: str  # refused | attempted | completed | failed | approval_required
    stage: str = ""
    reason: str = ""
    error_class: str = ""
    duration_ms: float = 0.0
    output: dict[str, Any] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None
    model_fault: bool = False
    verified: bool = False

    def diagnostic(self) -> dict[str, Any]:
        return {"tool": self.name, "status": self.status, "stage": self.stage,
                "reason": self.reason[:200], "error_class": self.error_class,
                "duration_ms": round(self.duration_ms, 1), "verified": self.verified,
                "approval": bool(self.pending_action)}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


class ToolBroker:
    def __init__(self, registry: ToolRegistry, evaluator: PermissionEvaluator | None = None,
                 *, timeout_s: float = 30.0, max_output_chars: int = 30_000,
                 local_only_tools: frozenset[str] = LOCAL_ONLY_TOOLS) -> None:
        self.registry = registry
        self.evaluator = evaluator or DefaultPermissionEvaluator()
        self.timeout_s = timeout_s
        self.max_output_chars = max_output_chars
        self.local_only_tools = local_only_tools

    # ---- 1. capability selection ----
    def select_tools(self, prompt: str, analysis: IntentAnalysis) -> list[str]:
        """Offer only tools plausibly relevant to the request; none for light conversation."""
        names = self.registry.names()
        if analysis.category == "simple":
            return []
        if analysis.category == "tool" or analysis.complexity == "complex":
            return list(names)
        prompt_words = _words(prompt)
        offered = []
        for name in names:
            tool = self.registry.definition(name)
            vocabulary = _words(name.replace(".", " ").replace("_", " ")) | _words(tool.description)
            if prompt_words & vocabulary:
                offered.append(name)
        return offered

    def schemas_for(self, names: Iterable[str]) -> tuple[dict[str, Any], ...]:
        wanted = set(names)
        return tuple(s for s in self.registry.schemas() if s["function"]["name"] in wanted)

    # ---- 2-4. proposal -> decision ----
    def evaluate_call(self, name: Any, raw_arguments: Any, *, offered: set[str] | frozenset[str],
                      provider_local: bool = True,
                      allow_remote_file_context: bool = False,
                      ) -> tuple[GateDecision, dict[str, Any] | None]:
        if not isinstance(name, str) or not name:
            return GateDecision(False, "parse", "missing tool name", model_fault=True), None
        try:
            arguments = json.loads(raw_arguments or "{}") if isinstance(raw_arguments, str) else raw_arguments
        except json.JSONDecodeError:
            return GateDecision(False, "parse", "arguments are not valid JSON", model_fault=True), None
        if not isinstance(arguments, dict):
            return GateDecision(False, "parse", "arguments must be a JSON object", model_fault=True), None
        tool = self.registry.definition(name)
        if tool is None:
            return GateDecision(False, "registered", f"'{name[:60]}' is not a registered tool",
                                model_fault=True), None
        if name not in offered:
            return GateDecision(False, "offered", "tool was not offered for this request",
                                model_fault=True), None
        verdict = self.evaluator.evaluate(tool, arguments)
        if not verdict.allowed:
            return verdict, None
        if name in self.local_only_tools and not provider_local and not allow_remote_file_context:
            return GateDecision(False, "egress",
                                "file and knowledge contents are blocked from non-local providers "
                                "by the data-egress policy"), None
        error = self.registry.validate_arguments(name, arguments)
        if error:
            return GateDecision(False, "validation", error, model_fault=True), None
        return GateDecision(True, "ok"), arguments

    # ---- 5. execution ----
    @staticmethod
    def classify_failure(output: dict[str, Any]) -> str:
        error_type = str(output.get("error_type") or "")
        if error_type in _TRANSIENT_ERRORS:
            return "transient"
        if error_type == "PermissionError":
            return "permission"
        if error_type in {"KeyError", "ValueError", "TypeError"}:
            return "validation"
        return "permanent"

    async def execute_call(self, name: str, arguments: dict[str, Any]) -> ToolCallRecord:
        started = time.perf_counter()
        record = ToolCallRecord(name=name, status="attempted", stage="execution")
        try:
            result: ToolResult = await asyncio.wait_for(
                self.registry.execute(name, arguments), timeout=self.timeout_s)
        except TimeoutError:
            record.status, record.error_class = "failed", "transient"
            record.reason = f"tool timed out after {self.timeout_s:g}s"
            record.output = {"ok": False, "error": record.reason}
            record.duration_ms = (time.perf_counter() - started) * 1000
            return record
        record.duration_ms = (time.perf_counter() - started) * 1000
        record.output = result.output
        if result.pending_action:
            record.status, record.pending_action = "approval_required", result.pending_action
        elif result.output.get("error") or result.output.get("ok") is False:
            record.status = "failed"
            record.reason = str(result.output.get("error") or "tool reported failure")[:300]
            record.error_class = self.classify_failure(result.output)
        else:
            record.status = "completed"
        return record

    async def call(self, name: Any, raw_arguments: Any, *, offered: set[str] | frozenset[str],
                   provider_local: bool = True, allow_remote_file_context: bool = False,
                   ) -> ToolCallRecord:
        decision, arguments = self.evaluate_call(
            name, raw_arguments, offered=offered, provider_local=provider_local,
            allow_remote_file_context=allow_remote_file_context)
        if not decision.allowed or arguments is None:
            return ToolCallRecord(
                name=str(name)[:80] if name else "", status="refused", stage=decision.stage,
                reason=decision.reason, error_class="permission" if decision.stage in
                {"permission", "egress", "offered"} else "validation",
                model_fault=decision.model_fault,
                output={"ok": False, "error": f"Call refused ({decision.stage}): {decision.reason}"})
        return await self.execute_call(str(name), arguments)

    def tool_message_content(self, record: ToolCallRecord) -> str:
        return json.dumps(record.output, ensure_ascii=False, default=str)[: self.max_output_chars]


ReasonFn = Any  # async (PlanStep, Plan) -> StepOutcome


class BrokerStepExecutor:
    """Runs plan steps: tool steps through the broker, reasoning steps through a callback."""

    def __init__(self, broker: ToolBroker, reason: ReasonFn | None = None) -> None:
        self.broker, self.reason = broker, reason
        self.records: list[ToolCallRecord] = []

    async def execute(self, step: PlanStep, plan: Plan) -> StepOutcome:
        if step.kind == "ask":
            return StepOutcome(False, error=step.title, error_class="needs_input")
        if step.kind == "reason":
            if self.reason is None:
                return StepOutcome(False, error="no reasoning backend is available",
                                   error_class="permanent")
            return await self.reason(step, plan)
        record = await self.broker.call(step.tool_name, step.arguments, offered={step.tool_name or ""})
        self.records.append(record)
        if record.status == "completed":
            summary = json.dumps(record.output, ensure_ascii=False, default=str)[:1500]
            return StepOutcome(True, summary, evidence={"tool": record.name, "output": record.output})
        if record.status == "approval_required":
            return StepOutcome(False, f"{record.name} needs owner approval",
                               error_class="needs_approval", pending_action=record.pending_action)
        return StepOutcome(False, error=record.reason or "tool failed", error_class=record.error_class,
                           evidence={"tool": record.name})
