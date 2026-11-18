"""Governed model-callable tool registry.

Tools are explicit contracts. The model can request only registered read-only
or preview-only operations; this registry has no shell or direct-mutation tool.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolResult:
    output: dict[str, Any]
    pending_action: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    permission: str
    mode: str
    handler: ToolHandler


class ToolRegistry:
    """Runtime registry for narrowly scoped and schema-validated tools."""

    MAX_ARGUMENT_BYTES = 2_200_000

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any],
        permission: str,
        mode: str,
        handler: ToolHandler,
    ) -> None:
        if not name or name in self._tools:
            raise ValueError(f"tool name is blank or already registered: {name}")
        if mode not in {"read", "preview"}:
            raise ValueError("tool mode must be read or preview")
        if not permission:
            raise ValueError("tool permission is required")
        if parameters.get("type") != "object":
            raise ValueError("tool parameters must use an object schema")
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            permission=permission,
            mode=mode,
            handler=handler,
        )

    def schemas(self) -> list[dict[str, Any]]:
        """Return function schemas supported by OpenAI-compatible tool callers."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (
                        f"{tool.description} Permission: {tool.permission}. "
                        f"Execution mode: {tool.mode}."
                    ),
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def definitions(self) -> list[dict[str, str]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission,
                "mode": tool.mode,
            }
            for tool in self._tools.values()
        ]

    def is_read_only(self, name: str) -> bool:
        """Return whether a registered tool is explicitly classified as read-only."""
        tool = self._tools.get(name)
        return tool is not None and tool.mode == "read"

    def read_only_contracts(self) -> list[dict[str, Any]]:
        """Return schemas for registered read-only tools only."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission,
                "parameters": tool.parameters,
            }
            for tool in self._tools.values()
            if tool.mode == "read"
        ]

    def validate_read_only_call(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        """Validate a proposed read-only call without invoking its handler."""
        tool = self._tools.get(name)
        if tool is None:
            return "Tool is not registered"
        if tool.mode != "read":
            return "Only registered read-only tools may be proposed for task execution"
        return self.validate_arguments(name, arguments)

    def validate_arguments(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Validate one tool's arguments without invoking its handler."""
        tool = self._tools.get(name)
        if tool is None:
            return "Tool is not registered"
        if not isinstance(arguments, dict):
            return "Tool arguments must be a JSON object"
        try:
            encoded_size = len(
                json.dumps(arguments, ensure_ascii=False).encode("utf-8")
            )
        except (TypeError, ValueError):
            return "Tool arguments are not valid JSON data"
        if encoded_size > self.MAX_ARGUMENT_BYTES:
            return "Tool arguments exceed the size limit"

        schema = tool.parameters
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        unknown = set(arguments).difference(properties)
        missing = required.difference(arguments)
        if unknown:
            return f"Unexpected argument(s): {', '.join(sorted(unknown))}"
        if missing:
            return f"Missing argument(s): {', '.join(sorted(missing))}"

        for key, value in arguments.items():
            rule = properties[key]
            expected = rule.get("type")
            if expected == "string" and not isinstance(value, str):
                return f"{key} must be a string"
            if expected == "integer" and (
                not isinstance(value, int) or isinstance(value, bool)
            ):
                return f"{key} must be an integer"
            if expected == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                return f"{key} must be a number"
            if expected == "boolean" and not isinstance(value, bool):
                return f"{key} must be a boolean"
            max_length = rule.get("maxLength")
            if max_length is not None and isinstance(value, str) and len(value) > max_length:
                return f"{key} exceeds its maximum length"
            minimum = rule.get("minimum")
            maximum = rule.get("maximum")
            if minimum is not None and isinstance(value, (int, float)) and value < minimum:
                return f"{key} is below its minimum"
            if maximum is not None and isinstance(value, (int, float)) and value > maximum:
                return f"{key} exceeds its maximum"
        return None

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult({"ok": False, "error": "Tool is not registered"})

        validation_error = self.validate_arguments(name, arguments)
        if validation_error:
            return ToolResult({"ok": False, "error": validation_error})

        try:
            # Invoke the handler in a worker thread so synchronous local I/O
            # cannot block FastAPI's event loop. If it returns an awaitable,
            # await that result on the current event loop.
            result = await asyncio.to_thread(tool.handler, arguments)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, ToolResult):
                return result
            if isinstance(result, dict):
                return ToolResult(result)
            return ToolResult({"ok": True, "result": result})
        except Exception as exc:
            return ToolResult({
                "ok": False,
                "error": str(exc)[:1000],
                "error_type": type(exc).__name__,
            })

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)
