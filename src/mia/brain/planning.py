"""Plan generation: model-proposed when useful, deterministic otherwise (SPS-03).

Model output is untrusted. It is parsed against a strict schema; tool steps must
name a registered read-only tool with schema-valid arguments, otherwise the plan
is rejected and a deterministic plan is used instead. Simple requests never reach
this module (see ``IntentAnalysis.wants_plan``).
"""

from __future__ import annotations

import json
from typing import Any

from mia.agent.tools import ToolRegistry
from mia.gateway.contracts import ModelCandidate, ModelRequest, RoutePolicy
from mia.gateway.service import ModelGateway

from .intent import IntentAnalysis
from .plan import MAX_STEPS, Plan, PlanError, PlanStep, new_plan
from .router import BrainRouter, NoSuitableModel


def heuristic_plan(objective: str, analysis: IntentAnalysis | None = None,
                   conversation_id: str | None = None) -> Plan:
    steps = [
        PlanStep("1", "Clarify the requested outcome and constraints", "reason"),
        PlanStep("2", "Gather the relevant information and context", "reason", depends_on=["1"]),
        PlanStep("3", "Work through the task", "reason", depends_on=["2"]),
        PlanStep("4", "Check the result against the objective", "reason", depends_on=["3"]),
        PlanStep("5", "Report the outcome and any remaining blockers", "reason", depends_on=["4"]),
    ]
    return new_plan(objective, steps, conversation_id,
                    constraints=["deterministic fallback plan"])


def parse_model_plan(raw: str, objective: str, tools: ToolRegistry | None,
                     conversation_id: str | None = None) -> Plan:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError("model did not return valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) - {"steps", "constraints"} or "steps" not in payload:
        raise PlanError("plan must be an object with a steps array")
    raw_steps = payload["steps"]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= MAX_STEPS:
        raise PlanError(f"plan must contain 1-{MAX_STEPS} steps")
    steps: list[PlanStep] = []
    allowed = {"id", "title", "kind", "tool_name", "arguments", "depends_on"}
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict) or set(item) - allowed or not {"title", "kind"} <= set(item):
            raise PlanError(f"step {index} has an invalid schema")
        title, kind = item["title"], item["kind"]
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise PlanError(f"step {index} title is invalid")
        if kind not in {"reason", "tool", "ask"}:
            raise PlanError(f"step {index} kind is invalid")
        deps = item.get("depends_on", [])
        if not isinstance(deps, list) or not all(isinstance(d, (str, int)) for d in deps):
            raise PlanError(f"step {index} depends_on is invalid")
        step = PlanStep(str(item.get("id", index)), title.strip(), kind,
                        depends_on=[str(d) for d in deps])
        if kind == "tool":
            name, args = item.get("tool_name"), item.get("arguments", {})
            if tools is None or not isinstance(name, str):
                raise PlanError(f"step {index} names a tool but none can be used")
            error = tools.validate_read_only_call(name, args if isinstance(args, dict) else {})
            if error:
                raise PlanError(f"step {index} has an invalid tool call: {error}")
            step.tool_name, step.arguments = name, args
        steps.append(step)
    constraints = payload.get("constraints", [])
    if not isinstance(constraints, list) or not all(isinstance(c, str) for c in constraints):
        raise PlanError("constraints must be a list of strings")
    return new_plan(objective, steps, conversation_id, constraints[:10])


async def generate_plan(
    *,
    objective: str,
    analysis: IntentAnalysis,
    gateway: ModelGateway,
    router: BrainRouter,
    candidates: list[ModelCandidate],
    tools: ToolRegistry | None,
    conversation_id: str | None = None,
    policy: RoutePolicy = RoutePolicy(),
) -> tuple[Plan, dict[str, Any]]:
    """Return (plan, info). Falls back to the deterministic plan and says why."""
    contracts = tools.read_only_contracts() if tools else []
    prompt = (
        "You are Mia's planning component. Break the objective into 2-"
        f"{MAX_STEPS} ordered steps. Return ONLY JSON: "
        '{"steps":[{"id":"1","title":"...","kind":"reason|tool|ask","tool_name":null,'
        '"arguments":{},"depends_on":[]}],"constraints":["..."]}. '
        "Use kind=tool only with a supplied read-only tool and exact arguments; use kind=ask "
        "when information is missing. Never invent tools. Never plan sending, deleting, "
        "purchasing or other external or irreversible actions.\n\nAVAILABLE TOOLS (trusted):\n"
        + json.dumps(contracts, sort_keys=True)
        + f"\n\nOBJECTIVE (untrusted):\n{objective[:4000]}"
    )
    request = ModelRequest(task_type="planning", prompt=prompt, max_output_tokens=1600,
                           required_capabilities=frozenset({"chat"}))
    info: dict[str, Any] = {"source": "model"}
    try:
        decision = router.route(candidates, request, task_type="planning", policy=policy)
        result = await gateway.complete(request, candidates, policy,
                                        order_hint=list(decision.ordered))
        if result.tool_calls:
            raise PlanError("planning responses must not contain tool calls")
        plan = parse_model_plan(result.content, objective, tools, conversation_id)
        info.update(provider=result.provider_id, model=result.model_id)
        return plan, info
    except (PlanError, NoSuitableModel, RuntimeError, KeyError) as exc:
        info = {"source": "deterministic_fallback", "reason": str(exc)[:300]}
        return heuristic_plan(objective, analysis, conversation_id), info
