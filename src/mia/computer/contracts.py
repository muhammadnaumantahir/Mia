"""Reusable capability contracts for computer and OS integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolContract:
    tool_id: str
    name: str
    description: str
    permission: str
    requires_approval: bool
    side_effects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_id: str
    success: bool
    output: Any
    audit_event: str | None = None


READ_DIRECTORY = ToolContract(
    tool_id="filesystem.list",
    name="List directory",
    description="List entries within an explicitly configured filesystem root.",
    permission="filesystem.read",
    requires_approval=False,
)
READ_FILE = ToolContract(
    tool_id="filesystem.read",
    name="Read text file",
    description="Read a bounded UTF-8 text file within an explicitly configured root.",
    permission="filesystem.read",
    requires_approval=False,
)
WRITE_FILE = ToolContract(
    tool_id="filesystem.write",
    name="Write text file",
    description="Write a text file only after a matching preview has been approved.",
    permission="filesystem.write",
    requires_approval=True,
    side_effects=("create_or_replace_file",),
)
