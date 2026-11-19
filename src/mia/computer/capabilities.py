"""Unified computer capability contract and registry.

Every computer action Mia can take is declared once here: what it is, where it runs,
its input/output shape, the permission level and risk it needs, its timeout, execution
constraints and how its result is verified. The registry is data only; enforcement lives
in :mod:`mia.computer.permissions` and in each adapter.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from mia.computer.contracts import ToolContract
from mia.computer.environment import EnvironmentInfo


class PermissionLevel(IntEnum):
    OBSERVE = 1
    READ = 2
    WRITE = 3
    EXECUTE = 4
    INTERACT = 5
    ADMINISTRATIVE = 6


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


ALL_OS = ("windows", "linux", "macos")


@dataclass(frozen=True, slots=True)
class HealthResult:
    available: bool
    reason: str = ""


HealthCheck = Callable[[EnvironmentInfo], HealthResult]


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    name: str
    description: str
    permission: PermissionLevel
    risk: RiskLevel
    timeout_seconds: float
    verification: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    supported_os: tuple[str, ...] = ALL_OS
    constraints: tuple[str, ...] = ()
    requires_modules: tuple[str, ...] = ()
    needs_display: bool = False
    side_effects: tuple[str, ...] = ()
    health_check: HealthCheck | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise ValueError("capability name must look like 'area.action'")
        if not self.description.strip() or not self.verification.strip():
            raise ValueError("capability needs a description and a verification method")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600:
            raise ValueError("capability timeout must be between 0 and 3600 seconds")
        for schema in (self.input_schema, self.output_schema):
            if schema.get("type") != "object":
                raise ValueError("capability schemas must be object schemas")
        if not self.supported_os or any(o not in ALL_OS for o in self.supported_os):
            raise ValueError("capability supported_os must be a subset of windows/linux/macos")

    @property
    def requires_approval_by_default(self) -> bool:
        """Approval is the default for anything that changes state or is risky."""
        return self.permission >= PermissionLevel.WRITE or self.risk >= RiskLevel.MEDIUM

    def to_contract(self) -> ToolContract:
        return ToolContract(
            tool_id=self.name,
            name=self.name,
            description=self.description,
            permission=self.permission.name.lower(),
            requires_approval=self.requires_approval_by_default,
            side_effects=self.side_effects,
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "permission": self.permission.name.lower(),
            "risk": self.risk.name.lower(),
            "timeout_seconds": self.timeout_seconds,
            "verification": self.verification,
            "supported_os": list(self.supported_os),
            "constraints": list(self.constraints),
            "requires_approval_by_default": self.requires_approval_by_default,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        if spec.name in self._items:
            raise ValueError(f"capability already registered: {spec.name}")
        self._items[spec.name] = spec

    def get(self, name: str) -> CapabilitySpec:
        try:
            return self._items[name]
        except KeyError:
            raise KeyError(f"unknown capability: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._items)

    def specs(self, *, os_family: str | None = None) -> list[CapabilitySpec]:
        items = [self._items[n] for n in self.names()]
        if os_family is not None:
            items = [s for s in items if os_family in s.supported_os]
        return items

    def check(self, name: str, env: EnvironmentInfo) -> HealthResult:
        spec = self.get(name)
        if env.os_family not in spec.supported_os:
            return HealthResult(False, f"not supported on {env.os_family}")
        for module in spec.requires_modules:
            try:
                found = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                found = False
            if not found:
                return HealthResult(False, f"optional dependency '{module}' is not installed")
        if spec.needs_display and not env.has_display:
            return HealthResult(False, "no interactive display session detected")
        if spec.health_check is not None:
            try:
                return spec.health_check(env)
            except Exception as exc:  # a failing probe means unavailable, never a crash
                return HealthResult(False, f"health check failed: {type(exc).__name__}")
        return HealthResult(True)

    def health(self, env: EnvironmentInfo) -> list[dict[str, Any]]:
        report = []
        for spec in self.specs():
            result = self.check(spec.name, env)
            report.append(
                {"name": spec.name, "available": result.available, "reason": result.reason}
            )
        return report


def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "additionalProperties": False}


_S = {"type": "string"}
_I = {"type": "integer"}


def default_registry() -> CapabilityRegistry:
    """Declare the capabilities that already have working adapters in this repository.

    New adapters register their own specs in the milestone that implements them; nothing is
    declared here that cannot actually run.
    """
    reg = CapabilityRegistry()
    R, W, X, N, V = (  # read, write, execute, interact (N), observe (V)
        PermissionLevel.READ,
        PermissionLevel.WRITE,
        PermissionLevel.EXECUTE,
        PermissionLevel.INTERACT,
        PermissionLevel.OBSERVE,
    )
    low, med, high = RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH
    root = ("Only inside owner-configured roots; traversal and symlink escapes are rejected.",)
    specs = [
        CapabilitySpec(
            "filesystem.list", "List a directory inside a configured root.", R, low, 10,
            "Returned entries come from a fresh directory scan.",
            _obj(root_id=_S, relative_path=_S), _obj(items={"type": "array"}),
            constraints=root + ("At most 200 entries.",),
        ),
        CapabilitySpec(
            "filesystem.read", "Read a bounded UTF-8 text file inside a configured root.", R, low, 10,
            "Content hash is returned with the text.",
            _obj(root_id=_S, relative_path=_S), _obj(content=_S),
            constraints=root + ("At most 2 MB.",),
        ),
        CapabilitySpec(
            "filesystem.search", "Search file names inside a configured root.", R, low, 20,
            "Each hit is re-checked to exist under the root.",
            _obj(root_id=_S, query=_S), _obj(items={"type": "array"}), constraints=root,
        ),
        CapabilitySpec(
            "filesystem.write", "Create or replace a text file after an approved preview.", W, med, 20,
            "Written content hash is compared with the approved hash.",
            _obj(root_id=_S, relative_path=_S, content=_S), _obj(sha256=_S),
            constraints=root + ("Credential, .env and .git files are blocked.",),
            side_effects=("create_or_replace_file",),
        ),
        CapabilitySpec(
            "filesystem.metadata", "Inspect size, type, modified time and hash of a path.", R, low, 10,
            "Values come from a fresh stat of the resolved path.",
            _obj(root_id=_S, relative_path=_S), _obj(kind=_S), constraints=root,
        ),
        CapabilitySpec(
            "filesystem.search_content", "Search text inside files under a folder.", R, low, 30,
            "Hits are re-read from files; secret-looking lines are redacted.",
            _obj(root_id=_S, query=_S, relative_path=_S), _obj(items={"type": "array"}),
            constraints=root + ("At most 2000 files of 1 MB each; credential files skipped.",),
        ),
        CapabilitySpec(
            "filesystem.compare", "Show a text diff between two files.", R, low, 20,
            "Both files are read fresh and compared byte-for-byte for equality.",
            _obj(root_id=_S, path_a=_S, path_b=_S), _obj(identical={"type": "boolean"}),
            constraints=root + ("At most 512 KB per file.",),
        ),
        CapabilitySpec(
            "filesystem.duplicates", "Find duplicate files by size then SHA-256.", R, low, 60,
            "Duplicate groups share identical SHA-256 digests.",
            _obj(root_id=_S, relative_path=_S), _obj(groups={"type": "array"}),
            constraints=root + ("At most 5000 files per scan.",),
        ),
        CapabilitySpec(
            "filesystem.inspect_project", "Summarize a project folder (type, tree, README, docs).", R, low, 30,
            "Counts and excerpts are read from the folder at call time.",
            _obj(root_id=_S, relative_path=_S), _obj(project_types={"type": "array"}),
            constraints=root,
        ),
        CapabilitySpec(
            "filesystem.mkdir", "Create a folder after approval.", W, med, 120,
            "Folder exists and is not a link afterwards.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("mkdir_filesystem_item",),
        ),
        CapabilitySpec(
            "filesystem.rename", "Rename a file or folder in place after approval.", W, med, 120,
            "Source gone and destination content hash equals the approved hash.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("rename_filesystem_item",),
        ),
        CapabilitySpec(
            "filesystem.copy", "Copy a file or folder to a new name after approval.", W, med, 120,
            "Destination tree hash equals the approved source hash.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("copy_filesystem_item",),
        ),
        CapabilitySpec(
            "filesystem.move", "Move a file or folder to a new location after approval.", W, med, 120,
            "Source gone and destination hash equals the approved hash.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("move_filesystem_item",),
        ),
        CapabilitySpec(
            "filesystem.delete", "Move a file or folder into the recovery area after approval.", W, high, 120,
            "Item is absent from its place and present in recovery with equal hash.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("delete_filesystem_item",),
        ),
        CapabilitySpec(
            "filesystem.restore", "Restore a deleted item from recovery after approval.", W, med, 120,
            "Restored hash equals the manifest hash.",
            _obj(root_id=_S, relative_path=_S, destination_path=_S), _obj(verified={"type": "boolean"}),
            constraints=root + ("Never overwrites; credential and .git paths are blocked.",),
            side_effects=("restore_filesystem_item",),
        ),
        CapabilitySpec(
            "apps.list", "List owner-allowlisted applications.", V, low, 5,
            "Entries come from the owner allowlist file.", _obj(), _obj(items={"type": "array"}),
        ),
        CapabilitySpec(
            "apps.launch", "Launch one allowlisted application with fixed arguments.", X, med, 30,
            "Process ID returned and process existence is checked after launch.",
            _obj(app_id=_S), _obj(process_id=_I),
            constraints=("Fixed argv, shell=False; shells and script hosts are blocked.",),
            side_effects=("start_process",),
        ),
        CapabilitySpec(
            "browser.navigate", "Open a public http(s) page in an isolated browser context.", V, low, 60,
            "Final URL and page title are read back after navigation.",
            _obj(url=_S), _obj(url=_S, title=_S),
            requires_modules=("playwright",),
            constraints=("Public hosts only; loopback/private ranges blocked on every request.",),
        ),
        CapabilitySpec(
            "browser.click", "Click an element in the isolated browser after approval.", N, med, 30,
            "Page URL must be unchanged at execution time as approved.",
            _obj(selector=_S), _obj(status=_S), requires_modules=("playwright",),
            side_effects=("page_interaction",),
        ),
        CapabilitySpec(
            "desktop.list_windows", "List visible desktop windows (title, handle, pid, class).", V, low, 10,
            "Windows are enumerated from the UI Automation tree.",
            _obj(), _obj(items={"type": "array"}),
            supported_os=("windows",), requires_modules=("pywinauto",), needs_display=True,
        ),
        CapabilitySpec(
            "desktop.type_text", "Replace text in a visible text control after approval.", N, high, 30,
            "Control is re-resolved and hash-bound to the approved text before typing.",
            _obj(window=_S, control_id=_S, text=_S), _obj(status=_S),
            supported_os=("windows",), requires_modules=("pywinauto",), needs_display=True,
            side_effects=("ui_text_entry",),
        ),
        CapabilitySpec(
            "system.resources", "Sample CPU and RAM usage.", V, low, 10,
            "Values are read from the OS counters at call time.",
            _obj(), _obj(cpu_percent={"type": "number"}), requires_modules=("psutil",),
        ),
    ]
    for spec in specs:
        reg.register(spec)
    return reg
