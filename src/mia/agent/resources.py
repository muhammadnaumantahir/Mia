"""Resource-aware system inspection without shell access or process secrets."""

from __future__ import annotations

from typing import Any


class ResourceMonitor:
    """On-demand CPU/RAM and bounded process metadata for the local machine."""

    def __init__(self, *, sample_interval: float = 0.2, psutil_module: Any = None) -> None:
        if not 0 <= sample_interval <= 2:
            raise ValueError("sample_interval must be between 0 and 2 seconds")
        self.sample_interval = sample_interval
        self._psutil_module = psutil_module

    def _psutil(self):
        if self._psutil_module is None:
            try:
                import psutil
            except ImportError as exc:
                raise RuntimeError(
                    'Install Mia with the "computer" extra to inspect system resources.'
                ) from exc
            self._psutil_module = psutil
        return self._psutil_module

    def snapshot(self) -> dict[str, float | int]:
        psutil = self._psutil()
        cpu_percent = float(psutil.cpu_percent(interval=self.sample_interval))
        memory = psutil.virtual_memory()
        process_count = sum(1 for _ in psutil.process_iter(["pid"]))
        return {
            "cpu_percent": max(0.0, min(100.0, cpu_percent)),
            "memory_percent": max(0.0, min(100.0, float(memory.percent))),
            "memory_available_bytes": int(memory.available),
            "memory_total_bytes": int(memory.total),
            "process_count": process_count,
        }

    def list_processes(self, *, limit: int = 50) -> list[dict[str, int | str | None]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        psutil = self._psutil()
        items = []
        for process in psutil.process_iter(["pid", "name", "status"]):
            try:
                info = process.info
                items.append({
                    "pid": int(info["pid"]),
                    "name": str(info.get("name") or "unknown")[:160],
                    "status": str(info.get("status") or "unknown")[:40],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            except (KeyError, TypeError, ValueError):
                continue
            if len(items) >= limit:
                break
        return items
