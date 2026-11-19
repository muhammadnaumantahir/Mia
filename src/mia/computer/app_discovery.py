"""Discover installed applications (names only). Discovery never launches anything.

Launching still requires an owner allowlist entry (see ApplicationLauncher).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

_UNINSTALL_KEYS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


class ApplicationDiscovery:
    MAX_ITEMS = 1000

    def __init__(self, *, os_family: str, environ: Any = None, winreg_module: Any = None,
                 home: Path | None = None, extra_dirs: dict[str, list[Path]] | None = None) -> None:
        self.os_family = os_family
        self.environ = os.environ if environ is None else environ
        self._winreg = winreg_module
        self.home = home or Path.home()
        self.extra_dirs = extra_dirs or {}

    def discover(self) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}

        def add(name: str, source: str, **extra: Any) -> None:
            name = name.strip()[:160]
            if name and name.casefold() not in found and len(found) < self.MAX_ITEMS:
                found[name.casefold()] = {"name": name, "source": source, **extra}

        if self.os_family == "windows":
            self._windows(add)
        elif self.os_family == "linux":
            self._linux(add)
        elif self.os_family == "macos":
            for base in self.extra_dirs.get("macos", [Path("/Applications")]):
                if base.is_dir():
                    for item in sorted(base.glob("*.app")):
                        add(item.stem, "applications_folder")
        return sorted(found.values(), key=lambda a: a["name"].casefold())

    def search(self, query: str) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("Search query is required")
        needle = query.casefold()
        return [a for a in self.discover() if needle in a["name"].casefold()]

    def _windows(self, add: Any) -> None:
        dirs = self.extra_dirs.get("windows")
        if dirs is None:
            dirs = []
            for var in ("PROGRAMDATA", "APPDATA"):
                base = self.environ.get(var)
                if base:
                    dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
        for base in dirs:
            if base.is_dir():
                for item in sorted(base.rglob("*.lnk"))[:2000]:
                    if "uninstall" not in item.stem.casefold():
                        add(item.stem, "start_menu")
        winreg = self._winreg
        if winreg is None:
            try:
                import winreg  # type: ignore[no-redef]
            except ImportError:
                return
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key_path in _UNINSTALL_KEYS:
                try:
                    root = winreg.OpenKey(hive, key_path)
                except OSError:
                    continue
                for index in range(2000):
                    try:
                        sub = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    try:
                        key = winreg.OpenKey(root, sub)
                        name = winreg.QueryValueEx(key, "DisplayName")[0]
                    except OSError:
                        continue
                    try:
                        system_component = winreg.QueryValueEx(key, "SystemComponent")[0]
                    except OSError:
                        system_component = 0
                    if not name or system_component == 1:
                        continue
                    publisher = version = None
                    for attr in ("Publisher", "DisplayVersion"):
                        try:
                            value = str(winreg.QueryValueEx(key, attr)[0])[:80]
                        except OSError:
                            value = None
                        if attr == "Publisher":
                            publisher = value
                        else:
                            version = value
                    add(str(name), "installed_programs", publisher=publisher, version=version)

    def _linux(self, add: Any) -> None:
        dirs = self.extra_dirs.get("linux")
        if dirs is None:
            dirs = [Path("/usr/share/applications"), self.home / ".local/share/applications"]
        for base in dirs:
            if not base.is_dir():
                continue
            for item in sorted(base.glob("*.desktop"))[:2000]:
                try:
                    text = item.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                entry = text.split("[Desktop Entry]", 1)[-1].split("\n[", 1)[0]
                if re.search(r"^(NoDisplay|Hidden)=true", entry, re.M):
                    continue
                match = re.search(r"^Name=(.+)$", entry, re.M)
                if match:
                    add(match.group(1), "desktop_entry")
