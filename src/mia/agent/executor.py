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

    MAX_STEPS = 5

    def validate_steps(self, task: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """Validate a single read call or a short, explicit read-only sequence."""
        if task.get("status") not in {"approved", "running"}:
            raise TaskExecutionError("Task must be approved before execution")

        metadata = task.get("metadata")
        if not isinstance(metadata, dict):
            raise TaskExecutionError("Task metadata is invalid")
        execution = metadata.get("execution")
        if not isinstance(execution, dict):
            raise TaskExecutionError("Task requires a structured metadata.execution contract")

        if set(execution) == {"tool_name", "arguments"}:
            raw_steps = [execution]
        elif set(execution) == {"steps"}:
            raw_steps = execution["steps"]
            if not isinstance(raw_steps, list) or not 2 <= len(raw_steps) <= self.MAX_STEPS:
                raise TaskExecutionError(
                    f"Multi-step execution must contain between 2 and {self.MAX_STEPS} steps"
                )
        else:
            raise TaskExecutionError(
                "Execution must contain tool_name and arguments, or a steps array only"
            )

        validated: list[tuple[str, dict[str, Any]]] = []
        for index, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict) or set(step) != {"tool_name", "arguments"}:
                raise TaskExecutionError(
                    f"Execution step {index} must contain tool_name and arguments only"
                )
            tool_name = step["tool_name"]
            arguments = step["arguments"]
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise TaskExecutionError(
                    f"Execution step {index} tool_name must be a non-empty string"
                )
            if not isinstance(arguments, dict):
                raise TaskExecutionError(f"Execution step {index} arguments must be an object")
            validation_error = self.tools.validate_read_only_call(tool_name, arguments)
            if validation_error:
                raise TaskExecutionError(f"Execution step {index}: {validation_error}")
            validated.append((tool_name, arguments))
        return validated

    def validate(self, task: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Backward-compatible validator for single-tool task contracts."""
        steps = self.validate_steps(task)
        if len(steps) != 1:
            raise TaskExecutionError(
                "Task contains multiple steps; use validate_steps to inspect the sequence"
            )
        return steps[0]

    async def execute(self, task: dict[str, Any]) -> dict[str, Any]:
        steps = self.validate_steps(task)
        results: list[dict[str, Any]] = []
        try:
            async with asyncio.timeout(self.timeout_seconds):
                for index, (tool_name, arguments) in enumerate(steps, start=1):
                    result = await self.tools.execute(tool_name, arguments)
                    if result.pending_action is not None:
                        raise TaskExecutionError(
                            f"Execution step {index} requested a preview action; "
                            "the read-only executor cannot complete it"
                        )
                    if result.output.get("error") or result.output.get("ok") is False:
                        raise TaskExecutionError(
                            f"Read-only execution step {index} failed: "
                            f"{str(result.output.get('error') or 'tool reported failure')[:300]}"
                        )
                    results.append({
                        "tool_name": tool_name,
                        "result": result.output,
                    })
        except TimeoutError as exc:
            raise TaskExecutionError(
                "Read-only task sequence exceeded its overall time limit"
            ) from exc

        if len(results) == 1:
            return results[0]
        return {
            "step_count": len(results),
            "tool_names": [item["tool_name"] for item in results],
            "steps": results,
        }
