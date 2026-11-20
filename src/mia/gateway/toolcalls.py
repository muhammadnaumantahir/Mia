"""Screening of model-proposed tool calls and structured output.

The gateway never executes tools. This module only checks what a model *proposed*
against what the caller *offered* in the request, so that a hallucinated tool name,
malformed arguments or an oversized payload is reported before it reaches the
caller's authorization pipeline. It is a first line of defence: Mia's permission
broker and capability registry still make the authoritative decision.

Names must match an offered tool exactly (never by similarity). Arguments must be
a JSON object that satisfies the offered tool's parameter schema; unknown keys
are rejected unless the schema allows them. Issue text is short and never
contains argument values.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .jsonschema_lite import validate_json

MAX_TOOL_CALLS = 8
MAX_ARGUMENT_CHARS = 100_000
MAX_STRUCTURED_CHARS = 1_000_000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ToolCallIssue:
    index: int
    name: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"index": self.index, "name": self.name, "reason": self.reason}


def _offered_schemas(tools: Any) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    for tool in tools or ():
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            parameters = function.get("parameters")
            schemas[function["name"]] = parameters if isinstance(parameters, dict) else {}
    return schemas


def _safe_name(name: Any) -> str:
    return _CONTROL.sub("", name if isinstance(name, str) else "")[:60]


def screen_tool_calls(
    calls: Any,
    offered_tools: Any,
    *,
    strict: bool = True,
) -> tuple[tuple[dict[str, Any], ...], tuple[ToolCallIssue, ...]]:
    """Return ``(valid_calls, issues)``; valid calls are normalized to OpenAI shape.

    Normalization gives every call a unique string id and a canonical JSON-string
    ``arguments`` value. A call with an issue is not in ``valid_calls``.
    """
    schemas = _offered_schemas(offered_tools)
    valid: list[dict[str, Any]] = []
    issues: list[ToolCallIssue] = []
    seen_ids: set[str] = set()
    for index, call in enumerate(list(calls or ())[: MAX_TOOL_CALLS + 8]):
        function = call.get("function") if isinstance(call, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        label = _safe_name(name)
        if index >= MAX_TOOL_CALLS:
            issues.append(ToolCallIssue(index, label, "too many tool calls in one response"))
            continue
        if not isinstance(function, dict) or not isinstance(name, str) or not name:
            issues.append(ToolCallIssue(index, label, "missing tool name"))
            continue
        if name not in schemas:
            issues.append(ToolCallIssue(index, label, "tool was not offered for this request"))
            continue
        raw = function.get("arguments", {})
        if isinstance(raw, str):
            if len(raw) > MAX_ARGUMENT_CHARS:
                issues.append(ToolCallIssue(index, label, "arguments exceed the size limit"))
                continue
            try:
                arguments = json.loads(raw) if raw.strip() else {}
            except ValueError:
                issues.append(ToolCallIssue(index, label, "arguments are not valid JSON"))
                continue
        else:
            arguments = raw
        if not isinstance(arguments, dict):
            issues.append(ToolCallIssue(index, label, "arguments must be a JSON object"))
            continue
        try:
            encoded = json.dumps(arguments, ensure_ascii=False)
        except (TypeError, ValueError):
            issues.append(ToolCallIssue(index, label, "arguments are not valid JSON data"))
            continue
        if len(encoded) > MAX_ARGUMENT_CHARS:
            issues.append(ToolCallIssue(index, label, "arguments exceed the size limit"))
            continue
        problems = validate_json(arguments, {"type": "object", **schemas[name]}, strict=strict)
        if problems:
            issues.append(ToolCallIssue(index, label, "; ".join(problems)[:300]))
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 200:
            call_id = f"call_{uuid.uuid4().hex}"
        if call_id in seen_ids:
            issues.append(ToolCallIssue(index, label, "duplicate tool call id"))
            continue
        seen_ids.add(call_id)
        valid.append(
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": encoded}}
        )
    return tuple(valid), tuple(issues)


def parse_structured(text: str, schema: dict[str, Any]) -> tuple[Any, str | None]:
    """Parse model text as JSON and check it against ``schema``.

    Returns ``(data, None)`` on success or ``(None, problem)``. Tolerates a single
    Markdown code fence, which many models add. Nothing is executed or evaluated.
    """
    raw = (text or "").strip()
    if len(raw) > MAX_STRUCTURED_CHARS:
        return None, "structured output exceeds the size limit"
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return None, f"not valid JSON ({getattr(exc, 'msg', 'parse error')})"
    problems = validate_json(data, schema, strict=False)
    if problems:
        return None, "; ".join(problems)[:300]
    return data, None
