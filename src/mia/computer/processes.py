"""Process inspection, system status and approval-bound termination.

No command lines or environment variables are exposed (they commonly hold secrets).
Termination is bound to PID + name + creation time (defeats PID reuse), refuses critical OS,
security and Mia's own processes, refuses other users' processes, and verifies the result.
"""

from __future__ import annotations

import getpass
import os
import time
from datetime import UTC, datetime
from typing import Any

from .approvals import ApprovalStore
from .control import AutomationControl, activity_or_null

PROTECTED_NAMES = frozenset({
    "system", "system idle process", "registry", "memory compression", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "explorer.exe", "fontdrvhost.exe", "msmpeng.exe", "nissrv.exe", "securityhealthservice.exe",
    "sgrmbroker.exe", "mssense.exe", "init", "systemd", "launchd", "kernel_task", "loginwindow",
})


class ProcessAccessError(PermissionError):
    """The process is protected, belongs to someone else, or changed since approval."""


class ProcessManager:
    MAX_SCAN = 1000

    def __init__(
        self,
        approvals: ApprovalStore,
        *,
        psutil_module: Any = None,
        own_pid: int | None = None,
        current_user: str | None = None,
        sample_interval: float = 0.2,
        sleep: Any = time.sleep,
        control: AutomationControl | None = None,
    ) -> None:
        self.control = control
        self.approvals = approvals
        self._psutil_module = psutil_module
        self.own_pid = own_pid if own_pid is not None else os.getpid()
        self._user = current_user
        self.sample_interval = sample_interval
        self._sleep = sleep

    def _psutil(self):
        if self._psutil_module is None:
            try:
                import psutil
            except ImportError as exc:
                raise RuntimeError('Install Mia with the "computer" extra.') from exc
            self._psutil_module = psutil
        return self._psutil_module

    def _current_user(self) -> str:
        if self._user is None:
            try:
                self._user = getpass.getuser()
            except Exception:
                self._user = ""
        return self._user

    def _protected_pids(self) -> set[int]:
        pids = {self.own_pid}
        try:
            proc = self._psutil().Process(self.own_pid)
            pids.update(p.pid for p in proc.parents())
        except Exception:
            pass
        return pids

    @staticmethod
    def _iso(ts: float | None) -> str | None:
        return datetime.fromtimestamp(ts, UTC).isoformat() if ts else None

    def _same_user(self, proc: Any) -> bool:
        try:
            owner = str(proc.username() or "")
        except Exception:
            return False
        return owner.split("\\")[-1].casefold() == self._current_user().casefold() != ""

    def _protection_reason(self, proc: Any, name: str) -> str | None:
        if proc.pid <= 4:
            return "system process"
        if proc.pid in self._protected_pids():
            return "Mia or its parent process"
        if name.casefold() in PROTECTED_NAMES:
            return "critical operating-system or security process"
        if not self._same_user(proc):
            return "process belongs to another user or its owner cannot be verified"
        return None

    # ---- read-only ------------------------------------------------------------------------
    def info(self, pid: int) -> dict[str, Any]:
        psutil = self._psutil()
        try:
            proc = psutil.Process(int(pid))
            with proc.oneshot():
                name = str(proc.name())[:160]
                mem = proc.memory_info().rss
                data = {
                    "pid": proc.pid, "name": name, "status": str(proc.status())[:40],
                    "memory_rss_bytes": int(mem), "threads": int(proc.num_threads()),
                    "started": self._iso(proc.create_time()),
                }
            data["cpu_percent"] = float(proc.cpu_percent(interval=self.sample_interval))
        except psutil.NoSuchProcess as exc:
            raise ProcessAccessError("No such process") from exc
        except psutil.AccessDenied as exc:
            raise ProcessAccessError("Access to that process is denied") from exc
        reason = self._protection_reason(proc, name)
        data["terminable"] = reason is None
        data["protected_reason"] = reason
        return data

    def top(self, *, by: str = "memory", limit: int = 10) -> list[dict[str, Any]]:
        if by not in {"memory", "cpu"}:
            raise ValueError("by must be memory or cpu")
        psutil = self._psutil()
        procs = []
        for proc in psutil.process_iter(["pid", "name"]):
            procs.append(proc)
            if len(procs) >= self.MAX_SCAN:
                break
        if by == "cpu":
            for proc in procs:
                try:
                    proc.cpu_percent(None)
                except Exception:
                    pass
            self._sleep(self.sample_interval)
        rows = []
        for proc in procs:
            try:
                rows.append({
                    "pid": proc.pid, "name": str(proc.info.get("name") or "unknown")[:160],
                    "memory_rss_bytes": int(proc.memory_info().rss),
                    "cpu_percent": float(proc.cpu_percent(None)) if by == "cpu" else None,
                })
            except Exception:
                continue
        key = "memory_rss_bytes" if by == "memory" else "cpu_percent"
        rows.sort(key=lambda r: -(r[key] or 0))
        return rows[: max(1, min(limit, 50))]

    def system_status(self) -> dict[str, Any]:
        psutil = self._psutil()
        mem = psutil.virtual_memory()
        disks = []
        for part in psutil.disk_partitions(all=False)[:16]:
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            disks.append({"mount": part.mountpoint, "total": usage.total, "free": usage.free,
                          "percent_used": usage.percent})
        up = []
        try:
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            for name, st in stats.items():
                if st.isup and not name.lower().startswith(("lo", "loopback")) and addrs.get(name):
                    up.append(name)
        except Exception:
            pass
        return {
            "cpu_percent": float(psutil.cpu_percent(interval=self.sample_interval)),
            "memory_percent": float(mem.percent), "memory_available_bytes": int(mem.available),
            "memory_total_bytes": int(mem.total), "disks": disks,
            "network_interfaces_up": sorted(up),
            "network_note": "Interface state only; no outbound connectivity test is performed.",
            "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        }

    # ---- termination ----------------------------------------------------------------------
    def _target(self, pid: int) -> tuple[Any, dict[str, Any]]:
        psutil = self._psutil()
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        try:
            proc = psutil.Process(pid)
            name = str(proc.name())[:160]
            created = proc.create_time()
        except psutil.NoSuchProcess as exc:
            raise ProcessAccessError("No such process") from exc
        except psutil.AccessDenied as exc:
            raise ProcessAccessError("Access to that process is denied") from exc
        reason = self._protection_reason(proc, name)
        if reason:
            raise ProcessAccessError(f"Refusing to stop this process: {reason}")
        return proc, {"pid": pid, "name": name, "create_time": round(created, 3)}

    def preview_terminate(self, pid: int, *, force: bool = False) -> dict[str, Any]:
        _, payload = self._target(pid)
        payload["force"] = bool(force)
        approval = self.approvals.request(
            action_id="process.terminate", payload=payload,
            summary=f"{'Force-kill' if force else 'Stop'} {payload['name']} (PID {pid})",
        )
        return {**approval, "target": payload}

    def execute_terminate(self, approval_id: str, pid: int, *, force: bool = False,
                          wait_seconds: float = 5.0) -> dict[str, Any]:
        if self.control is not None:
            self.control.assert_active()
        proc, payload = self._target(pid)
        payload["force"] = bool(force)
        self.approvals.consume(approval_id=approval_id, action_id="process.terminate",
                               payload=payload)
        with activity_or_null(self.control, "process.terminate",
                              f"Stopping {payload['name']} (PID {pid})"):
            return self._terminate(proc, payload, force, wait_seconds)

    def _terminate(self, proc: Any, payload: dict[str, Any], force: bool,
                   wait_seconds: float) -> dict[str, Any]:
        psutil = self._psutil()
        try:
            proc.kill() if force else proc.terminate()
        except psutil.NoSuchProcess:
            return {**payload, "verified": True, "status": "already_exited"}
        except psutil.AccessDenied as exc:
            raise ProcessAccessError("Access to that process is denied") from exc
        try:
            proc.wait(timeout=wait_seconds)
        except psutil.TimeoutExpired:
            pass
        except psutil.NoSuchProcess:
            pass
        alive = False
        try:
            alive = bool(proc.is_running()) and str(proc.status()) != "zombie"
        except psutil.NoSuchProcess:
            alive = False
        return {**payload, "verified": not alive,
                "status": "still_running" if alive else "stopped"}
