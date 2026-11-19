"""Allowlisted desktop application launching with one-time approval."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import ApprovalStore


class ApplicationLaunchError(RuntimeError):
    """The requested application cannot be safely launched."""


_BLOCKED_EXECUTABLES = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "wscript.exe",
    "cscript.exe",
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "sh",
    "bash",
    "zsh",
    "fish",
}


@dataclass(frozen=True, slots=True)
class ApplicationDefinition:
    app_id: str
    display_name: str
    executable: str
    arguments: tuple[str, ...] = ()
    description: str = ""


class ApplicationLauncher:
    """Launch only applications declared in an owner-managed JSON allowlist."""

    def __init__(
        self,
        applications: list[ApplicationDefinition],
        approvals: ApprovalStore,
        *,
        launch_check_seconds: float = 1.0,
    ) -> None:
        self.approvals = approvals
        self.launch_check_seconds = launch_check_seconds
        self._applications: dict[str, ApplicationDefinition] = {}
        for app in applications:
            key = app.app_id.strip().lower()
            if not key or key in self._applications:
                raise ValueError(f"Invalid or duplicate application ID: {app.app_id}")
            self._applications[key] = app

    @classmethod
    def from_json(
        cls,
        path: str | Path,
        approvals: ApprovalStore,
    ) -> ApplicationLauncher:
        config_path = Path(path)
        if not config_path.exists():
            return cls([], approvals)
        payload: dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8"))
        definitions = []
        for item in payload.get("applications", []):
            definitions.append(
                ApplicationDefinition(
                    app_id=str(item["app_id"]),
                    display_name=str(item["display_name"]),
                    executable=str(item["executable"]),
                    arguments=tuple(str(arg) for arg in item.get("arguments", [])),
                    description=str(item.get("description", "")),
                )
            )
        return cls(definitions, approvals)

    def list_applications(self) -> list[dict[str, str]]:
        items = []
        for app in self._applications.values():
            try:
                executable = self._resolve_executable(app.executable)
                status = "available" if executable else "not_found"
            except ApplicationLaunchError:
                status = "blocked"
            items.append({
                "app_id": app.app_id,
                "display_name": app.display_name,
                "description": app.description,
                "status": status,
            })
        return items

    def preview_launch(self, app_id: str) -> dict[str, str]:
        app = self._get_application(app_id)
        executable = self._resolve_executable(app.executable)
        payload = {
            "app_id": app.app_id,
            "executable": executable,
            "arguments": list(app.arguments),
        }
        return {
            **self.approvals.request(
                action_id="process.launch",
                payload=payload,
                summary=f"Launch {app.display_name}",
            ),
            "app_id": app.app_id,
            "display_name": app.display_name,
            "executable": executable,
        }

    def execute_launch(self, app_id: str, approval_id: str) -> dict[str, str | int]:
        app = self._get_application(app_id)
        executable = self._resolve_executable(app.executable)
        payload = {
            "app_id": app.app_id,
            "executable": executable,
            "arguments": list(app.arguments),
        }
        self.approvals.consume(
            approval_id=approval_id,
            action_id="process.launch",
            payload=payload,
        )
        try:
            process = subprocess.Popen(
                [executable, *app.arguments],
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise ApplicationLaunchError(f"The application could not be started: {exc.strerror}") from exc
        time.sleep(self.launch_check_seconds)
        code = process.poll() if callable(getattr(process, "poll", None)) else None
        if code not in (None, 0):
            raise ApplicationLaunchError(
                f"{app.display_name} exited immediately with code {code}; the launch failed"
            )
        return {
            "app_id": app.app_id,
            "display_name": app.display_name,
            "process_id": process.pid,
            "launch_state": "running" if code is None else "handed_off_or_exited_cleanly",
            "verified": code is None,
        }

    def _get_application(self, app_id: str) -> ApplicationDefinition:
        try:
            return self._applications[app_id.strip().lower()]
        except KeyError as exc:
            raise ApplicationLaunchError("Application is not in the allowlist") from exc

    @staticmethod
    def _resolve_executable(executable: str) -> str:
        candidate = Path(executable).expanduser()
        name = candidate.name.lower()
        if name in _BLOCKED_EXECUTABLES or candidate.suffix.lower() in {
            ".bat", ".cmd", ".ps1", ".sh"
        }:
            raise ApplicationLaunchError("Shell and script launch targets are blocked")
        resolved = str(candidate.resolve()) if candidate.is_absolute() else shutil.which(executable)
        if not resolved:
            raise ApplicationLaunchError("Allowlisted application executable was not found")
        if Path(resolved).name.lower() in _BLOCKED_EXECUTABLES:
            raise ApplicationLaunchError("Shell and script launch targets are blocked")
        return resolved
