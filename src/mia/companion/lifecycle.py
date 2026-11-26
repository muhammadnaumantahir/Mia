"""Least-privilege Windows lifecycle manager for the outbound Mia companion."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable


TASK_NAME = "Mia Companion"
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


class CompanionLifecycleError(RuntimeError):
    """A companion lifecycle operation failed safely."""


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".companion-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class WindowsCompanionLifecycle:
    """Install/start/stop/status/restart the companion without service elevation."""

    def __init__(
        self,
        identity,
        *,
        task_name: str = TASK_NAME,
        python_executable: str | None = None,
        platform_name: str | None = None,
        run: Callable[..., Any] = subprocess.run,
        popen: Callable[..., Any] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        process_alive: Callable[[int], bool] = _process_is_alive,
    ) -> None:
        self.identity = identity
        self.home = Path(identity.home)
        self.task_name = task_name
        self.python_executable = str(python_executable or sys.executable)
        self.platform_name = platform_name or sys.platform
        self.run_process = run
        self.spawn_process = popen
        self.sleep = sleep
        self.monotonic = monotonic
        self.process_alive = process_alive
        self.runtime_dir = self.home / "runtime"
        self.stop_path = self.runtime_dir / "stop.request"
        self.status_path = self.runtime_dir / "status.json"
        self.process_path = self.runtime_dir / "process.json"
        self.log_path = self.runtime_dir / "companion.log"

    def _require_windows(self) -> None:
        if self.platform_name != "win32":
            raise CompanionLifecycleError("The Windows companion lifecycle is available on Windows only")

    def _require_confirmation(self, confirmed: bool, operation: str) -> None:
        if not confirmed:
            raise CompanionLifecycleError(
                f"{operation} changes the current-user Windows logon task; pass --confirm to proceed"
            )

    def _run(self, arguments: list[str], *, timeout: int = 20) -> Any:
        try:
            result = self.run_process(
                arguments, capture_output=True, text=True, check=False,
                timeout=timeout, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CompanionLifecycleError(
                f"Windows task scheduler command failed ({type(exc).__name__})"
            ) from exc
        if result.returncode != 0:
            reason = (result.stderr or result.stdout or "").strip()
            if len(reason) > 240:
                reason = reason[:240]
            raise CompanionLifecycleError(
                "Windows Task Scheduler refused the operation"
                + (f": {reason}" if reason else "")
            )
        return result

    def _task_exists(self) -> bool:
        self._require_windows()
        try:
            result = self.run_process(
                ["schtasks.exe", "/Query", "/TN", self.task_name],
                capture_output=True, text=True, check=False, timeout=15, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    @staticmethod
    def _ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def install(self, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_windows()
        self._require_confirmation(confirmed, "install")
        if not self.identity.exists:
            raise CompanionLifecycleError("Pair this companion before installing its lifecycle task")
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        # RegisterTaskDefinition with an Interactive principal uses the currently
        # logged-on user's token; no password is stored or requested. Limited is the
        # least-privilege run level. No SYSTEM account, elevation or hidden persistence.
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            "$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name",
            "$action = New-ScheduledTaskAction -Execute "
            + self._ps_quote(self.python_executable)
            + " -Argument '-m mia.companion.cli run' -WorkingDirectory "
            + self._ps_quote(str(self.home)),
            "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user",
            "$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited",
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
            "Register-ScheduledTask -TaskName " + self._ps_quote(self.task_name)
            + " -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null",
        ])
        self._run([
            powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-Command", script,
        ])
        return {
            "installed": True,
            "task_name": self.task_name,
            "run_level": "LIMITED",
            "logon_type": "interactive_current_user",
            "password_stored": False,
            "elevated": False,
        }

    def uninstall(self, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_windows()
        self._require_confirmation(confirmed, "uninstall")
        stopped = self.stop(timeout_seconds=20)
        if not stopped:
            raise CompanionLifecycleError(
                "The companion has not stopped gracefully; the scheduled task was retained"
            )
        deleted = False
        if self._task_exists():
            self._run(["schtasks.exe", "/Delete", "/TN", self.task_name, "/F"])
            deleted = True
        return {"uninstalled": deleted, "stopped": stopped, "task_name": self.task_name}

    def start(self) -> dict[str, Any]:
        self._require_windows()
        if not self.identity.exists:
            raise CompanionLifecycleError("Pair this companion first with 'mia-companion pair'")
        if self.status().get("running"):
            return {"started": False, "already_running": True, "status": self.status()}
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.stop_path.unlink()
        except FileNotFoundError:
            pass
        if self._task_exists():
            self._run(["schtasks.exe", "/Run", "/TN", self.task_name])
            return {"started": True, "scheduled_task": True, "task_name": self.task_name}

        log = self.log_path.open("a", encoding="utf-8")
        try:
            process = self.spawn_process(
                [self.python_executable, "-m", "mia.companion.cli", "run"],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(Path.cwd()),
                close_fds=True,
                creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
                shell=False,
            )
        except OSError as exc:
            log.close()
            raise CompanionLifecycleError(
                f"Could not start the companion process ({type(exc).__name__})"
            ) from exc
        log.close()
        _atomic_json(self.process_path, {"pid": int(process.pid), "started_at": time.time()})
        return {"started": True, "scheduled_task": False, "pid": int(process.pid)}

    def stop(self, *, timeout_seconds: int = 20) -> bool:
        self._require_windows()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.stop_path.write_text("owner requested graceful stop\n", encoding="ascii")
        deadline = self.monotonic() + max(0, min(timeout_seconds, 60))
        while self.monotonic() < deadline:
            state = self._read_status()
            pid = int(state.get("pid") or self._read_process_pid() or 0)
            if state.get("state") == "stopped" or not self.process_alive(pid):
                try:
                    self.process_path.unlink()
                except FileNotFoundError:
                    pass
                return True
            self.sleep(0.5)
        # Do not kill the process behind the owner's back. Leave the stop marker in
        # place so the process exits at its next short control-loop checkpoint.
        return False

    def restart_update(self, *, confirmed: bool = False) -> dict[str, Any]:
        self._require_windows()
        self._require_confirmation(confirmed, "restart/update")
        stopped = self.stop(timeout_seconds=25)
        if not stopped:
            raise CompanionLifecycleError(
                "The companion has not stopped gracefully; no forced termination was attempted"
            )
        started = self.start()
        return {"restarted": True, "stopped": stopped, "started": started}

    def _read_status(self) -> dict[str, Any]:
        try:
            value = json.loads(self.status_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _read_process_pid(self) -> int:
        try:
            value = json.loads(self.process_path.read_text(encoding="utf-8"))
            return int(value.get("pid") or 0)
        except (OSError, ValueError, TypeError):
            return 0

    def status(self) -> dict[str, Any]:
        self._require_windows()
        data = self._read_status()
        pid = int(data.get("pid") or self._read_process_pid() or 0)
        running = self.process_alive(pid)
        installed = self._task_exists()
        if not running and data.get("state") not in {"unpaired", "error"}:
            state = "stopped"
        else:
            state = data.get("state") or ("running" if running else "stopped")
        result = {
            "installed": installed,
            "task_name": self.task_name,
            "running": running,
            "pid": pid if running else None,
            "state": state,
            "updated_at": data.get("updated_at"),
            "last_success": data.get("last_success"),
            "last_error": data.get("last_error"),
            "device_id": data.get("device_id"),
            "device_name": data.get("device_name"),
            "server_url": data.get("server_url"),
            "scopes": data.get("scopes", []),
            "outbound_only": True,
            "inbound_listener": False,
            "elevated": False,
            "run_level": "LIMITED",
        }
        return result
