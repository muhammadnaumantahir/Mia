"""Read-only usage, spend, latency, resource, storage and workload snapshot (MIA-LIFE-12).

Every section reports where its numbers come from (``source``), when they were taken
(``measured_at``) and a ``status``:

* ``ok``          - measured; ``None`` values inside still mean "not measured", never zero.
* ``unavailable`` - the measurement could not be taken; ``reason`` says why (no paths, no
                    exception text, so nothing machine-specific or secret leaks).

Nothing here writes, spawns processes or reads file contents. Storage inspection only stats
files inside the Mia data directory, does not follow symlinks and is bounded.
"""

from __future__ import annotations

import math
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAX_FILES_WALKED = 20_000
TOP_FILES = 5

_BUDGET_ENV = (
    ("per_request", "MIA_MAX_REQUEST_SPEND_USD"),
    ("daily", "MIA_MAX_DAILY_SPEND_USD"),
    ("monthly", "MIA_MAX_MONTHLY_SPEND_USD"),
)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _unavailable(source: str, now: datetime, reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "source": source, "measured_at": _iso(now), "reason": reason}


def _budget_limits() -> dict[str, Any]:
    """Configured spend ceilings as the gateway sees them (invalid/missing => not enforceable)."""
    limits: dict[str, Any] = {}
    for label, name in _BUDGET_ENV:
        raw = os.getenv(name, "").strip()
        if not raw:
            limits[label] = {"configured": False, "valid": False, "limit_usd": None}
            continue
        try:
            value = float(raw)
        except ValueError:
            limits[label] = {"configured": True, "valid": False, "limit_usd": None}
            continue
        valid = math.isfinite(value) and value >= 0
        limits[label] = {"configured": True, "valid": valid, "limit_usd": value if valid else None}
    return {
        "allow_paid": os.getenv("MIA_ALLOW_PAID", "").strip().lower() == "true",
        "limits": limits,
    }


def _usage_section(ledger: Any, days: int, now: datetime) -> dict[str, Any]:
    source = "usage ledger (mia-usage.sqlite3)"
    if ledger is None:
        return _unavailable(source, now, "usage ledger is not configured")
    try:
        since = now - timedelta(days=days)
        window = ledger.window_summary(_iso(since), _iso(now + timedelta(seconds=1)))
        per_model = ledger.summary(days)["items"]
        today = ledger.spend_today_utc()
        month = ledger.spend_month_utc(now)
        reservations = ledger.reservation_summary(now)
    except Exception:  # noqa: BLE001 - a broken ledger must not break the whole dashboard
        return _unavailable(source, now, "usage ledger could not be read")
    notes = []
    if window["unknown_cost_rows"]:
        notes.append(
            f"{window['unknown_cost_rows']} paid request(s) have no known price; "
            "their cost is unknown, not zero."
        )
    if window["estimated_token_rows"]:
        notes.append(
            f"{window['estimated_token_rows']} request(s) used estimated rather than "
            "provider-reported token counts."
        )
    return {
        "status": "ok",
        "source": source,
        "measured_at": _iso(now),
        "window_days": days,
        "requests": window["requests"],
        "paid_requests": window["paid_requests"],
        "non_local_requests": window["non_local_requests"],
        "input_tokens": window["input_tokens"],
        "output_tokens": window["output_tokens"],
        "cost_usd": window["cost_usd"],  # None when any paid row's price is unknown
        "spend": {"today_utc_usd": today, "month_utc_usd": month},  # None = unknown
        "latency_ms": window["latency_ms"],  # count/mean/median/p95, None when unmeasured
        "by_model": per_model,
        "budget": _budget_limits(),
        "reservations": reservations,
        "notes": notes,
    }


def _bytes(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def _resources_section(
    monitor: Any, now: datetime, psutil_module: Any | None
) -> dict[str, Any]:
    source = "psutil (host CPU/RAM) and this Mia process"
    if monitor is None:
        return _unavailable(source, now, "resource monitor is not configured")
    try:
        host = monitor.snapshot()
    except Exception:  # noqa: BLE001 - psutil missing or unreadable
        return _unavailable(source, now, "system resource inspection is not available")
    process: dict[str, Any] = {"measured": False}
    try:
        psutil = psutil_module
        if psutil is None:
            import psutil  # type: ignore[no-redef]
        proc = psutil.Process()
        cpu = proc.cpu_times()
        process = {
            "measured": True,
            "memory_rss_bytes": _bytes(proc.memory_info().rss),
            # Cumulative CPU seconds since start; a rate would need two samples.
            "cpu_seconds_total": round(float(cpu.user) + float(cpu.system), 2),
            "threads": int(proc.num_threads()),
        }
    except Exception:  # noqa: BLE001
        process = {"measured": False, "reason": "process statistics are not available"}
    return {
        "status": "ok",
        "source": source,
        "measured_at": _iso(now),
        "cpu_percent": host.get("cpu_percent"),
        "memory_percent": host.get("memory_percent"),
        "memory_available_bytes": host.get("memory_available_bytes"),
        "memory_total_bytes": host.get("memory_total_bytes"),
        "process_count": host.get("process_count"),
        "mia_process": process,
    }


def _walk_data_dir(root: Path) -> tuple[int, int, bool, list[tuple[str, int]]]:
    """Bounded, symlink-safe size walk. Returns (bytes, files, truncated, largest files)."""
    total = files = 0
    truncated = False
    sizes: list[tuple[str, int]] = []
    stack = [root]
    while stack and not truncated:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    files += 1
                    total += size
                    sizes.append((str(Path(entry.path).relative_to(root)), size))
                    if files >= MAX_FILES_WALKED:
                        truncated = True
                        break
            except OSError:
                continue
    sizes.sort(key=lambda item: item[1], reverse=True)
    return total, files, truncated, sizes[:TOP_FILES]


def _storage_section(
    data_dir: Path | None, now: datetime, disk_usage: Callable[[Path], Any]
) -> dict[str, Any]:
    source = "filesystem stat of the Mia data directory (no file contents read)"
    if data_dir is None or not data_dir.is_dir():
        return _unavailable(source, now, "the Mia data directory does not exist yet")
    try:
        disk = disk_usage(data_dir)
        total, files, truncated, largest = _walk_data_dir(data_dir)
    except Exception:  # noqa: BLE001
        return _unavailable(source, now, "storage could not be inspected")
    return {
        "status": "ok",
        "source": source,
        "measured_at": _iso(now),
        "disk_total_bytes": _bytes(disk.total),
        "disk_used_bytes": _bytes(disk.used),
        "disk_free_bytes": _bytes(disk.free),
        "data_dir_bytes": total,
        "data_dir_files": files,
        # When true, data_dir_bytes/files are lower bounds (walk stopped at the file cap).
        "data_dir_truncated": truncated,
        "largest_files": [{"name": name, "bytes": size} for name, size in largest],
    }


def _workload_section(
    probes: dict[str, Callable[[], dict[str, Any]]] | None, now: datetime
) -> dict[str, Any]:
    source = "Mia's own scheduler/runtime status endpoints (flags only; no activity counts)"
    if not probes:
        return _unavailable(source, now, "no background-workload probes are registered")
    items = []
    for name, probe in sorted(probes.items()):
        try:
            state = probe()
            items.append({"name": name, "status": "ok", **state})
        except Exception:  # noqa: BLE001
            items.append({"name": name, "status": "unavailable",
                          "reason": "status could not be read"})
    return {"status": "ok", "source": source, "measured_at": _iso(now), "items": items}


def build_usage_dashboard(
    *,
    ledger: Any,
    resource_monitor: Any,
    data_dir: Path | None,
    workload_probes: dict[str, Callable[[], dict[str, Any]]] | None = None,
    days: int = 30,
    now: datetime | None = None,
    psutil_module: Any | None = None,
    disk_usage: Callable[[Path], Any] = shutil.disk_usage,
) -> dict[str, Any]:
    """Assemble the read-only dashboard payload. Each section degrades independently."""
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    days = max(1, min(int(days), 365))
    return {
        "generated_at": _iso(moment),
        "window_days": days,
        "usage": _usage_section(ledger, days, moment),
        "resources": _resources_section(resource_monitor, moment, psutil_module),
        "storage": _storage_section(data_dir, moment, disk_usage),
        "workload": _workload_section(workload_probes, moment),
    }
