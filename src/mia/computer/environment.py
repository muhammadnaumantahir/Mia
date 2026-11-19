"""Detect the computer environment Mia is running on (read-only, no side effects)."""

from __future__ import annotations

import getpass
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

_OS_FAMILIES = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}


@dataclass(frozen=True, slots=True)
class EnvironmentInfo:
    os_family: str
    os_name: str
    os_release: str
    os_version: str
    machine: str
    python_version: str
    cpu_count: int | None
    total_memory_bytes: int | None
    user: str | None
    has_display: bool
    shells: tuple[str, ...] = field(default_factory=tuple)
    disks: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_windows(self) -> bool:
        return self.os_family == "windows"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["shells"] = list(self.shells)
        data["disks"] = list(self.disks)
        data["is_windows"] = self.is_windows
        return data


def _detect_shells(os_family: str, which: Any) -> tuple[str, ...]:
    candidates = {
        "windows": ("powershell", "pwsh", "cmd"),
        "linux": ("bash", "sh", "pwsh"),
        "macos": ("zsh", "bash", "sh", "pwsh"),
    }.get(os_family, ("sh",))
    return tuple(name for name in candidates if which(name))


def _detect_display(os_family: str, environ: Any) -> bool:
    if os_family in {"windows", "macos"}:
        # An interactive desktop cannot be proven from here (services/SSH may lack one);
        # desktop adapters must still verify access when they run.
        return True
    return bool(environ.get("DISPLAY") or environ.get("WAYLAND_DISPLAY"))


def _disk_roots(os_family: str, disk_usage: Any, exists: Any) -> tuple[dict[str, Any], ...]:
    if os_family == "windows":
        roots = [f"{letter}:\\" for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ" if exists(f"{letter}:\\")]
    else:
        roots = ["/"]
    disks: list[dict[str, Any]] = []
    for root in roots[:8]:
        try:
            usage = disk_usage(root)
        except OSError:
            continue
        disks.append({"root": root, "total": usage.total, "free": usage.free})
    return tuple(disks)


def detect_environment(
    *,
    platform_module: Any = platform,
    environ: Any = None,
    which: Any = shutil.which,
    disk_usage: Any = shutil.disk_usage,
    exists: Any = os.path.exists,
    psutil_module: Any = None,
) -> EnvironmentInfo:
    """Collect OS, resource, session and tool availability facts.

    All collaborators are injectable so tests can simulate Windows on any host.
    """
    env = os.environ if environ is None else environ
    system = platform_module.system()
    family = _OS_FAMILIES.get(system, "other")
    total_memory: int | None = None
    try:
        if psutil_module is None:
            import psutil as psutil_module  # type: ignore[no-redef]
        total_memory = int(psutil_module.virtual_memory().total)
    except Exception:  # psutil is optional; absence is reported as unknown, not zero
        total_memory = None
    try:
        user: str | None = getpass.getuser()
    except Exception:
        user = None
    return EnvironmentInfo(
        os_family=family,
        os_name=system or "unknown",
        os_release=platform_module.release(),
        os_version=platform_module.version(),
        machine=platform_module.machine(),
        python_version=sys.version.split()[0],
        cpu_count=os.cpu_count(),
        total_memory_bytes=total_memory,
        user=user,
        has_display=_detect_display(family, env),
        shells=_detect_shells(family, which),
        disks=_disk_roots(family, disk_usage, exists),
    )
