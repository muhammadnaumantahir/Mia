"""Controlled terminal execution.

A model-generated command is never trusted. Before anything runs it is validated, classified,
confined to an owner-configured working directory, authorized (owner approval or an owner
grant) and given a timeout. Output is captured per stream, size-capped and redacted. The
child receives a scrubbed environment so Mia's own secrets are not inherited.

The command classifier is defence in depth for risk presentation and for refusing obviously
dangerous operations. It is not a sandbox; the approval step is the control that matters.
"""

from __future__ import annotations

import dataclasses
import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mia.memory.safety import find_secret

from .approvals import ApprovalError, ApprovalStore
from .capabilities import CapabilitySpec, RiskLevel
from .filesystem import FilesystemAdapter
from .permissions import PermissionPolicy

SHELLS = {"powershell", "pwsh", "cmd", "bash", "sh", "zsh"}


class CommandRefused(PermissionError):
    """The command is blocked or invalid and cannot be executed even with approval."""


@dataclass(frozen=True, slots=True)
class CommandAssessment:
    risk: RiskLevel
    blocked: bool
    reasons: tuple[str, ...]
    categories: tuple[str, ...] = ()


_BLOCKED_PROGRAMS = {
    "format", "diskpart", "bcdedit", "fdisk", "parted", "dd", "shutdown", "reboot", "halt",
    "poweroff", "mimikatz", "vssadmin", "wevtutil", "cipher", "sc", "schtasks", "reg", "regedit",
    "netsh", "sudo", "su", "doas", "runas", "passwd", "net", "net1", "takeown", "set-mppreference",
    "add-mppreference", "set-executionpolicy", "restart-computer", "stop-computer",
    "new-service", "set-service", "register-scheduledtask", "crontab", "systemctl", "launchctl",
}
_BLOCKED_PATTERNS = [
    (r"-enc(odedcommand)?\b", "encoded commands hide their intent"),
    (r"\binvoke-expression\b|\biex\b", "dynamic code evaluation is not allowed"),
    (r"downloadstring|downloadfile", "download-and-run patterns are not allowed"),
    (r"\|\s*(sudo\s+)?(sh|bash|zsh|pwsh|powershell|cmd|iex)\b", "piping into a shell is not allowed"),
    (r"\bmkfs(\.\w+)?\b", "disk formatting is not allowed"),
    (r"hk(lm|cu):|\\currentversion\\run\b|\\start menu\\programs\\startup", "registry/startup changes need a separate administrative path"),
    (r"(^|[\s'\"/\\])(\.env(\.\w+)?|id_rsa|id_ed25519|credentials\.json|secrets\.json|token\.json|\.npmrc|\.netrc|\.pypirc)(\s|$|['\"])", "credential files are off limits"),
    (r"\.(pem|pfx|p12|kdbx)\b|[/\\]\.(ssh|gnupg|aws|azure)[/\\]?", "credential/key locations are off limits"),
    (r"\b(gci|ls|dir|get-childitem)\s+env:|\$env:\s*$|\bprintenv\b|\bexport\s+-p\b", "dumping the environment is not allowed"),
    (r"\b(rm|rmdir|rd|del|erase|remove-item|ri)\b[^\n;&|]*\s(/|~|/\*|\*|[a-z]:\\?|[a-z]:\\\*|[a-z]:\\windows\S*|/etc\S*|/usr\S*|/bin\S*)(\s|$)", "recursive deletion of a drive root or system folder is not allowed"),
]
_BLOCKED_PATTERNS = [(re.compile(p, re.I), why) for p, why in _BLOCKED_PATTERNS]
_BLOCKED_ANYWHERE = re.compile(
    r"(?:^|[;&|(`\"'])\s*(sudo|su|doas|runas|mimikatz|diskpart|bcdedit|vssadmin|schtasks|"
    r"set-mppreference|set-executionpolicy|restart-computer|stop-computer|shutdown)\b", re.I)

_HIGH_PROGRAMS = {
    "rm", "rmdir", "rd", "del", "erase", "remove-item", "ri", "kill", "killall", "pkill",
    "taskkill", "stop-process", "chmod", "chown", "icacls", "attrib", "curl", "wget",
    "invoke-webrequest", "iwr", "invoke-restmethod", "irm", "ssh", "scp", "sftp", "ftp",
    "telnet", "nc", "ncat", "winget", "choco", "scoop", "apt", "apt-get", "yum", "dnf",
    "brew", "snap", "bash", "sh", "zsh", "cmd", "powershell", "pwsh", "wsl", "start-process",
    "mklink", "ln",
}
_HIGH_GIT = re.compile(
    r"\bgit\s+(reset\s+--hard|clean|push|pull|fetch|clone|remote\s+(add|set-url)|"
    r"checkout\s+--|restore|branch\s+-D|stash\s+(drop|clear)|rebase|filter-branch|gc\s+--prune)", re.I)
_MEDIUM_PROGRAMS = {"move-item", "set-content", "out-file", "add-content", "new-item", "mv", "move", "ren", "rename", "cp", "copy", "xcopy", "robocopy", "mkdir",
                    "md", "touch", "tee", "sed", "pip", "pip3", "npm", "npx", "yarn", "pnpm",
                    "dotnet", "cargo", "go", "make", "pytest", "python", "python3", "py", "node",
                    "git", "java", "javac", "gradle", "mvn", "tsc", "ruff", "black", "flutter",
                    "dart", "ruby", "php", "rustc", "cmake"}
_LOW_PROGRAMS = {"ls", "dir", "pwd", "echo", "whoami", "hostname", "date", "cat", "type", "head",
                 "tail", "wc", "findstr", "grep", "more", "get-childitem", "get-content",
                 "get-location", "select-string", "get-process", "get-date", "which", "where",
                 "sort", "uniq", "tree", "stat", "file", "du", "df"}
_LOW_GIT = {"status", "log", "diff", "show", "rev-parse", "ls-files", "describe", "blame", "branch"}
_VERSION_ONLY = {"--version", "-v", "-V", "version"}
_COMPOSITION = re.compile(r"[;&<>`]|\$\(|\$\{|\|\|")
_SPLIT = re.compile(r"&&|\|\||;|\||\n")


def _program(token: str) -> str:
    name = token.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _tokens(segment: str, windows: bool) -> list[str] | None:
    try:
        return shlex.split(segment, posix=not windows)
    except ValueError:
        return None


def classify_command(command: str, *, windows: bool = False) -> CommandAssessment:
    text = command.strip()
    if not text:
        raise CommandRefused("The command is empty")
    if len(text) > 4000 or "\x00" in text:
        raise CommandRefused("The command is too long or contains invalid characters")
    reasons: list[str] = []
    categories: set[str] = set()
    for pattern, why in _BLOCKED_PATTERNS:
        if pattern.search(text):
            return CommandAssessment(RiskLevel.CRITICAL, True, (why,), ("blocked",))
    if _BLOCKED_ANYWHERE.search(text):
        return CommandAssessment(
            RiskLevel.CRITICAL, True,
            ("administrative or system-level command; no administrative path is implemented",),
            ("blocked",))
    risk = RiskLevel.LOW
    segments = [s.strip() for s in _SPLIT.split(text) if s.strip()]
    for segment in segments:
        tokens = _tokens(segment, windows)
        if not tokens:
            risk = max(risk, RiskLevel.MEDIUM)
            reasons.append("could not parse part of the command")
            continue
        program = _program(tokens[0])
        args = [t.strip("\"'") for t in tokens[1:]]
        if program in _BLOCKED_PROGRAMS:
            return CommandAssessment(
                RiskLevel.CRITICAL, True,
                (f"'{program}' is administrative or system-level; no administrative path is implemented",),
                ("blocked",))
        if program in _HIGH_PROGRAMS or (program == "git" and _HIGH_GIT.search(segment)):
            risk = max(risk, RiskLevel.HIGH)
            categories.add("destructive_or_network_or_system")
            reasons.append(f"'{program}' can delete data, use the network or change the system")
        elif program == "git" and args and args[0] in _LOW_GIT and not any(
                a in {"-D", "-d", "-m", "-M", "--delete"} for a in args):
            pass
        elif program in _LOW_PROGRAMS:
            pass
        elif program in _MEDIUM_PROGRAMS and args and all(a in _VERSION_ONLY for a in args[:1]) \
                and len(args) == 1:
            pass
        else:
            risk = max(risk, RiskLevel.MEDIUM)
            reasons.append(f"'{program}' runs a program or changes files")
    if _COMPOSITION.search(text) and risk < RiskLevel.MEDIUM:
        risk = RiskLevel.MEDIUM
        reasons.append("command chaining or redirection")
    if re.search(r"(^|\s)>{1,2}\s*\S", text):
        categories.add("writes_file")
    if not reasons and risk == RiskLevel.LOW:
        reasons.append("read-only inspection command")
    return CommandAssessment(risk, False, tuple(dict.fromkeys(reasons)), tuple(sorted(categories)))


_SAFE_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP",
             "TMPDIR", "HOME", "USERPROFILE", "USERNAME", "LANG", "LC_ALL", "TERM", "SHELL",
             "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA", "LOCALAPPDATA", "APPDATA")
_AUTH_HEADER = re.compile(r"(?i)(authorization\s*[:=]\s*)(?:(?:bearer|basic|token)\s+)?\S+")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")
_KV_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|authorization|bearer)\b(\s*[:=]\s*)\S+")


def scrubbed_environment(environ: Any = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    return {k: env[k] for k in _SAFE_ENV if k in env}


def redact_output(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if find_secret(line):
            lines.append("[redacted: possible secret]")
        else:
            line = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}[redacted]", line)
            line = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", line)
            lines.append(_KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", line))
    return "\n".join(lines)


@dataclass(slots=True)
class Execution:
    execution_id: str
    command: str
    shell: str
    root_id: str
    cwd: str
    risk: str
    timeout: float
    status: str = "running"
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str | None = None
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    error: str | None = None
    approval_id: str | None = None
    authorized_by: str = "approval"
    _proc: Any = field(default=None, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id, "command": redact_output(self.command),
            "shell": self.shell, "root_id": self.root_id, "cwd": self.cwd, "risk": self.risk,
            "timeout_seconds": self.timeout, "status": self.status, "exit_code": self.exit_code,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "stdout": self.stdout, "stderr": self.stderr, "truncated": self.truncated,
            "error": self.error, "authorized_by": self.authorized_by,
            "verification": "exit code only: 0 means the process ended normally, not that the goal was met",
        }


class TerminalExecutor:
    MAX_OUTPUT_BYTES = 256 * 1024
    MAX_TIMEOUT = 600.0
    DEFAULT_TIMEOUT = 60.0
    MAX_CONCURRENT = 3
    MAX_HISTORY = 100

    def __init__(
        self,
        approvals: ApprovalStore,
        filesystem: FilesystemAdapter,
        *,
        os_family: str,
        available_shells: tuple[str, ...] = (),
        policy: PermissionPolicy | None = None,
        spec: CapabilitySpec | None = None,
        environ: Any = None,
        on_finish: Any = None,
    ) -> None:
        self.approvals = approvals
        self.on_finish = on_finish
        self.fs = filesystem
        self.os_family = os_family
        self.available_shells = tuple(available_shells)
        self.policy = policy
        self.spec = spec
        self._environ = environ
        self._executions: dict[str, Execution] = {}
        self._lock = threading.Lock()

    # ---- validation and planning ---------------------------------------------------------
    def default_shell(self) -> str:
        if self.os_family == "windows":
            return "powershell" if "powershell" in self.available_shells else "cmd"
        return "bash" if "bash" in self.available_shells else "sh"

    def _argv(self, shell: str, command: str) -> list[str]:
        exe = shutil.which(shell) or shell
        if shell in {"powershell", "pwsh"}:
            return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
        if shell == "cmd":
            return [exe, "/d", "/s", "/c", command]
        return [exe, "-c", command]

    def _plan(self, command: str, shell: str | None, root_id: str, cwd: str,
              timeout: float | None) -> tuple[dict[str, Any], CommandAssessment, Path]:
        shell = shell or self.default_shell()
        if shell not in SHELLS:
            raise CommandRefused("Unsupported shell")
        if self.available_shells and shell not in self.available_shells:
            raise CommandRefused(f"{shell} is not available on this computer")
        seconds = float(timeout if timeout is not None else self.DEFAULT_TIMEOUT)
        if not 0 < seconds <= self.MAX_TIMEOUT:
            raise ValueError(f"timeout must be between 0 and {int(self.MAX_TIMEOUT)} seconds")
        directory = self.fs._resolve(root_id, cwd or ".")
        if not directory.is_dir():
            raise FileNotFoundError("Working directory not found")
        assessment = classify_command(command, windows=shell in {"powershell", "pwsh", "cmd"})
        if assessment.blocked:
            raise CommandRefused("; ".join(assessment.reasons))
        rel = str(directory.relative_to(self.fs.roots[root_id])) if directory != self.fs.roots[root_id] else "."
        plan = {"command": command.strip(), "shell": shell, "root_id": root_id, "cwd": rel,
                "timeout": seconds, "risk": assessment.risk.name.lower()}
        return plan, assessment, directory

    def _decision(self, assessment: CommandAssessment, directory: Path):
        if self.policy is None or self.spec is None:
            return None
        spec = dataclasses.replace(self.spec, risk=max(self.spec.risk, assessment.risk))
        return self.policy.authorize(spec, path=str(directory))

    def preview(self, command: str, *, root_id: str, cwd: str = ".", shell: str | None = None,
                timeout: float | None = None) -> dict[str, Any]:
        plan, assessment, directory = self._plan(command, shell, root_id, cwd, timeout)
        decision = self._decision(assessment, directory)
        covered = bool(decision and decision.allowed and not decision.requires_approval)
        approval = self.approvals.request(
            action_id="terminal.run", payload=plan,
            summary=f"[{plan['risk']}] {plan['shell']} in {plan['cwd']}: {plan['command'][:160]}")
        return {**approval, "plan": plan, "reasons": list(assessment.reasons),
                "covered_by_owner_grant": covered,
                "policy_reason": decision.reason if decision else None}

    def start(self, command: str, *, root_id: str, cwd: str = ".", shell: str | None = None,
              timeout: float | None = None, approval_id: str | None = None) -> Execution:
        plan, assessment, directory = self._plan(command, shell, root_id, cwd, timeout)
        decision = self._decision(assessment, directory)
        authorized_by = "approval"
        if approval_id:
            self.approvals.consume(approval_id=approval_id, action_id="terminal.run", payload=plan)
        elif decision and decision.allowed and not decision.requires_approval:
            authorized_by = f"grant:{decision.grant_id}"
        else:
            raise ApprovalError("Owner approval is required before this command can run")
        with self._lock:
            if sum(1 for e in self._executions.values() if e.status == "running") >= self.MAX_CONCURRENT:
                raise CommandRefused("Too many commands are already running")
            execution = Execution(
                execution_id=uuid.uuid4().hex, command=plan["command"], shell=plan["shell"],
                root_id=root_id, cwd=plan["cwd"], risk=plan["risk"], timeout=plan["timeout"],
                started_at=datetime.now(UTC).isoformat(), approval_id=approval_id,
                authorized_by=authorized_by)
            self._executions[execution.execution_id] = execution
            self._prune()
        threading.Thread(target=self._run, args=(execution, directory), daemon=True).start()
        return execution

    def _prune(self) -> None:
        if len(self._executions) > self.MAX_HISTORY:
            finished = [e for e in self._executions.values() if e.status != "running"]
            finished.sort(key=lambda e: e.started_at)
            for old in finished[: len(self._executions) - self.MAX_HISTORY]:
                self._executions.pop(old.execution_id, None)

    # ---- running ---------------------------------------------------------------------------
    def _run(self, ex: Execution, directory: Path) -> None:
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        threads: list[threading.Thread] = []
        try:
            kwargs: dict[str, Any] = {}
            if self.os_family == "windows":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | \
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                self._argv(ex.shell, ex.command), cwd=directory, shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=scrubbed_environment(self._environ), close_fds=True, **kwargs)
            ex._proc = proc

            def drain(stream: Any, name: str) -> None:
                buf = buffers[name]
                while chunk := stream.read(4096):
                    room = self.MAX_OUTPUT_BYTES - len(buf)
                    if room > 0:
                        buf.extend(chunk[:room])
                    if len(chunk) > room:
                        ex.truncated = True

            for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
                t = threading.Thread(target=drain, args=(stream, name), daemon=True)
                t.start()
                threads.append(t)
            deadline = time.monotonic() + ex.timeout
            outcome = "completed"
            while proc.poll() is None:
                if ex._cancel.is_set():
                    outcome = "cancelled"
                    self._kill_tree(proc)
                    break
                if time.monotonic() > deadline:
                    outcome = "timed_out"
                    self._kill_tree(proc)
                    break
                time.sleep(0.02)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            for t in threads:
                t.join(timeout=2)
            ex.exit_code = proc.returncode
            ex.status = outcome if outcome != "completed" else (
                "completed" if proc.returncode == 0 else "failed")
        except Exception as exc:  # start failure is reported, never raised into the API thread
            ex.status = "failed"
            ex.error = f"{type(exc).__name__}: could not start or monitor the command"
        finally:
            ex.stdout = redact_output(bytes(buffers["stdout"]).decode("utf-8", errors="replace"))
            ex.stderr = redact_output(bytes(buffers["stderr"]).decode("utf-8", errors="replace"))
            ex.finished_at = datetime.now(UTC).isoformat()
            ex._done.set()
            if self.on_finish is not None:
                try:
                    self.on_finish(ex)
                except Exception:
                    pass

    @staticmethod
    def _kill_tree(proc: Any) -> None:
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            parent.kill()
            return
        except Exception:
            pass
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10, check=False)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---- control ---------------------------------------------------------------------------
    def get(self, execution_id: str) -> Execution:
        try:
            return self._executions[execution_id]
        except KeyError:
            raise KeyError("Unknown execution") from None

    def wait(self, execution_id: str, timeout: float) -> Execution:
        ex = self.get(execution_id)
        ex._done.wait(timeout=max(0.0, min(timeout, self.MAX_TIMEOUT)))
        return ex

    def cancel(self, execution_id: str) -> bool:
        ex = self.get(execution_id)
        if ex.status != "running":
            return False
        ex._cancel.set()
        return True

    def stop_all(self) -> int:
        count = 0
        for ex in list(self._executions.values()):
            if ex.status == "running":
                ex._cancel.set()
                count += 1
        return count

    def list_executions(self) -> list[dict[str, Any]]:
        items = sorted(self._executions.values(), key=lambda e: e.started_at, reverse=True)
        return [{k: v for k, v in e.public().items() if k not in {"stdout", "stderr"}}
                for e in items]
