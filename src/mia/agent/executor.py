"""Read-only execution boundary for explicitly approved tasks."""

from __future__ import annotations

import asyncio
from typing import Any

from .tools import ToolRegistry


class TaskExecutionError(RuntimeError):
    """A task is not safe or ready for execution."""


class TaskToolExecutor:
    """Execute only one registered read-only tool from an approved task.

    Task descriptions are never interpreted as commands. A task must carry an
    explicit metadata.execution object naming a registered read-only tool
    and its structured arguments. Preview/mutation tools are deliberately
    excluded and remain on the separate approval-card path.
    """

    def __init__(self, tools: ToolRegistry, *, timeout_seconds: float = 30.0) -> None:
        if not 1.0 <= timeout_seconds <= 120.0:
            raise ValueError("timeout_seconds must be between 1 and 120")
        self.tools = tools
        self.timeout_seconds = timeout_seconds

    def validate(self, task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if task.get("status") != "approved":
            raise TaskExecutionError("Task must be approved before execution")

        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            raise TaskExecutionError("Task metadata is invalid")
        execution = metadata.get("execution")
        if not isinstance(execution, dict) or set(execution) != {"tool_name", "arguments"}:
            raise TaskExecutionError(
                "Task requires metadata.execution with tool_name and arguments"
            )

        tool_name = execution["tool_name"]
        arguments = execution["arguments"]
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise TaskExecutionError("Execution tool_name must be a non-empty string")
        if not isinstance(arguments, dict):
            raise TaskExecutionError("Execution arguments must be an object")
        if not self.tools.is_read_only(tool_name):
            raise TaskExecutionError(
                "Only registered read-only tools may run through the task executor"
            )
        return tool_name, arguments

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        tool_name, arguments = self.validate(task)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                result = await self.tools.execute(tool_name, arguments)
        except TimeoutError as exc:
            raise TaskExecutionError("Read-only task tool exceeded its time limit") from exc

        if result.pending_action is not None:
            raise TaskExecutionError(
                "A preview action cannot be completed by the read-only task executor"
            )
        if result.output.get("error") or result.output.get("ok") is False:
            raise TaskExecutionError("Read-only task tool reported an error")
        return {
            "tool_name": tool_name,
            "result": result.output,
        }
