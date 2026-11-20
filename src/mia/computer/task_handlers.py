"""Step handlers that connect the task engine to the real computer adapters.

Every handler delegates to the same preview/approve/execute code the direct APIs use, so a task
step has exactly the governance of the equivalent single action. Handlers only exist for adapters
that are present and supported on this machine.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from .approvals import ApprovalError
from .control import AutomationStopped
from .tasks import Inspection, Outcome, StepHandler

_TRANSIENT = (TimeoutError, ConnectionError, BlockingIOError)


def _guarded(fn: Callable[[], Outcome]) -> Outcome:
    try:
        return fn()
    except (ApprovalError, AutomationStopped):
        raise
    except Exception as exc:
        return Outcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:300],
                       retryable=isinstance(exc, _TRANSIENT))


async def _aguarded(fn: Callable[[], Any]) -> Outcome:
    try:
        return await fn()
    except (ApprovalError, AutomationStopped):
        raise
    except Exception as exc:
        return Outcome(ok=False, error=f"{type(exc).__name__}: {exc}"[:300],
                       retryable=isinstance(exc, _TRANSIENT))


def _read_only(capability: str, required: tuple[str, ...], fn: Callable[[dict], dict],
               summary: Callable[[dict], str]) -> StepHandler:
    return StepHandler(
        capability=capability, idempotent=True, required=required,
        inspect=lambda args: Inspection(summary=summary(args), risk="low"),
        run=lambda args, _a: _guarded(lambda: Outcome(ok=True, verified=True, data=fn(args))),
    )


def build_handlers(
    *, fs: Any = None, file_ops: Any = None, processes: Any = None, launcher: Any = None,
    discovery: Any = None, terminal: Any = None, browser: Any = None, browser_actions: Any = None,
    desktop: Any = None, desktop_adapter: Any = None, os_family: str = "linux",
) -> dict[str, StepHandler]:
    h: dict[str, StepHandler] = {}

    def add(handler: StepHandler) -> None:
        h[handler.capability] = handler

    # ---- filesystem -----------------------------------------------------------------------
    if fs is not None and file_ops is not None:
        def rel(a: dict) -> str:
            return str(a.get("relative_path", "."))
        add(_read_only("filesystem.list", ("root_id",),
                       lambda a: {"items": fs.list_directory(a["root_id"], rel(a))},
                       lambda a: f"List {rel(a)}"))
        add(_read_only("filesystem.read", ("root_id", "relative_path"),
                       lambda a: {**(r := fs.read_text_auto(a["root_id"], a["relative_path"])),
                                  "content": r["content"][:20000]},
                       lambda a: f"Read {a['relative_path']}"))
        add(_read_only("filesystem.search", ("root_id", "query"),
                       lambda a: {"items": fs.search_filenames(a["root_id"], a["query"])},
                       lambda a: f"Find files named like '{a['query']}'"))
        add(_read_only("filesystem.metadata", ("root_id", "relative_path"),
                       lambda a: file_ops.metadata(a["root_id"], a["relative_path"]),
                       lambda a: f"Inspect {a['relative_path']}"))
        add(_read_only("filesystem.search_content", ("root_id", "query"),
                       lambda a: {"items": file_ops.search_content(a["root_id"], a["query"], rel(a))},
                       lambda a: f"Search file contents for '{a['query']}'"))
        add(_read_only("filesystem.compare", ("root_id", "path_a", "path_b"),
                       lambda a: file_ops.compare_files(a["root_id"], a["path_a"], a["path_b"]),
                       lambda a: f"Compare {a['path_a']} and {a['path_b']}"))
        add(_read_only("filesystem.duplicates", ("root_id",),
                       lambda a: file_ops.find_duplicates(a["root_id"], rel(a)),
                       lambda a: f"Find duplicate files in {rel(a)}"))
        add(_read_only("filesystem.inspect_project", ("root_id",),
                       lambda a: file_ops.inspect_project(a["root_id"], rel(a)),
                       lambda a: f"Inspect project {rel(a)}"))

        for op, risk in (("mkdir", "medium"), ("rename", "medium"), ("copy", "medium"),
                         ("move", "medium"), ("delete", "high"), ("restore", "medium")):
            def _kw(a: dict) -> dict:
                return {"destination_path": a.get("destination_path"), "recovery_id": a.get("recovery_id")}

            def inspect(a: dict, op=op, risk=risk) -> Inspection:
                plan = file_ops._plan(op, a["root_id"], a.get("relative_path", ""),
                                      a.get("destination_path"), a.get("recovery_id"))
                target = plan.get("source", plan.get("recovery_id"))
                extra = f" -> {plan['destination']}" if "destination" in plan else ""
                return Inspection(f"{op} {target}{extra}", risk, True)

            def request(a: dict, op=op) -> str:
                return file_ops.preview(op, a["root_id"], a.get("relative_path", ""), **_kw(a))["approval_id"]

            def run(a: dict, approval_id: str | None, op=op) -> Outcome:
                def go() -> Outcome:
                    result = file_ops.execute(op, approval_id, a["root_id"], a.get("relative_path", ""),
                                              **_kw(a))
                    return Outcome(ok=True, verified=result["verified"],
                                   data={k: v for k, v in result.items() if k != "operation"})
                return _guarded(go)
            add(StepHandler(f"filesystem.{op}", inspect, run, request,
                            required=("root_id",) if op == "restore" else ("root_id", "relative_path")))

        def w_inspect(a: dict) -> Inspection:
            path = fs._resolve(a["root_id"], a["relative_path"])
            fs._validate_write_path(path)
            return Inspection(f"write {a['relative_path']} ({len(a['content'])} chars)", "medium", True)

        def w_request(a: dict) -> str:
            return fs.preview_write(a["root_id"], a["relative_path"], a["content"])["approval_id"]

        def w_run(a: dict, approval_id: str | None) -> Outcome:
            def go() -> Outcome:
                result = fs.execute_write(approval_id=approval_id, root_id=a["root_id"],
                                          relative_path=a["relative_path"], content=a["content"])
                back = fs.read_text_auto(a["root_id"], a["relative_path"])["content"]
                return Outcome(ok=True, verified=back == a["content"],
                               data={"sha256": result["sha256"], "backup": result.get("backup")})
            return _guarded(go)
        add(StepHandler("filesystem.write", w_inspect, w_run, w_request,
                        required=("root_id", "relative_path", "content")))

    # ---- system and processes -------------------------------------------------------------
    if processes is not None:
        add(_read_only("system.status", (), lambda a: processes.system_status(),
                       lambda a: "Check CPU, memory, disk space and network interfaces"))
        add(_read_only("process.top", (), lambda a: {"items": processes.top(
            by=a.get("by", "memory"), limit=int(a.get("limit", 10)))},
            lambda a: f"List the top processes by {a.get('by', 'memory')}"))
        add(_read_only("process.info", ("pid",), lambda a: processes.info(int(a["pid"])),
                       lambda a: f"Inspect process {a['pid']}"))

        def p_inspect(a: dict) -> Inspection:
            _, payload = processes._target(int(a["pid"]))
            return Inspection(f"stop {payload['name']} (PID {payload['pid']})", "high", True)

        def p_request(a: dict) -> str:
            return processes.preview_terminate(int(a["pid"]), force=bool(a.get("force")))["approval_id"]

        def p_run(a: dict, approval_id: str | None) -> Outcome:
            def go() -> Outcome:
                r = processes.execute_terminate(approval_id, int(a["pid"]), force=bool(a.get("force")))
                return Outcome(ok=r["verified"], verified=r["verified"], data=r,
                               error=None if r["verified"] else "The process is still running")
            return _guarded(go)
        add(StepHandler("process.terminate", p_inspect, p_run, p_request, required=("pid",)))

    if discovery is not None:
        add(_read_only("apps.discover", (), lambda a: {"items": (
            discovery.search(a["query"]) if a.get("query") else discovery.discover())[:200]},
            lambda a: "List installed applications"))

    if launcher is not None:
        def l_inspect(a: dict) -> Inspection:
            apps = {x["app_id"]: x for x in launcher.list_applications()}
            if a["app_id"] not in apps:
                raise ValueError("That application is not on the owner allowlist")
            return Inspection(f"launch {apps[a['app_id']]['display_name']}", "medium", True)

        def l_run(a: dict, approval_id: str | None) -> Outcome:
            def go() -> Outcome:
                r = launcher.execute_launch(a["app_id"], approval_id)
                return Outcome(ok=True, verified=bool(r.get("verified")), data=r,
                               note=None if r.get("verified") else
                               "The process exited right away (it may have handed off to a running instance).")
            return _guarded(go)
        add(StepHandler("apps.launch", l_inspect, l_run,
                        lambda a: launcher.preview_launch(a["app_id"])["approval_id"],
                        required=("app_id",)))

    # ---- terminal -----------------------------------------------------------------------------
    if terminal is not None:
        def t_args(a: dict) -> dict:
            return {"root_id": a["root_id"], "cwd": a.get("relative_path", "."),
                    "shell": a.get("shell"), "timeout": a.get("timeout_seconds")}

        def t_inspect(a: dict) -> Inspection:
            plan, assessment, directory = terminal._plan(
                a["command"], a.get("shell"), a["root_id"], a.get("relative_path", "."),
                a.get("timeout_seconds"))
            decision = terminal._decision(assessment, directory)
            covered = bool(decision and decision.allowed and not decision.requires_approval)
            return Inspection(f"[{plan['risk']}] {plan['command'][:120]} in {plan['cwd']}",
                              plan["risk"], not covered)

        def t_request(a: dict) -> str:
            k = t_args(a)
            return terminal.preview(a["command"], root_id=k["root_id"], cwd=k["cwd"], shell=k["shell"],
                                    timeout=k["timeout"])["approval_id"]

        def t_run(a: dict, approval_id: str | None) -> Outcome:
            def go() -> Outcome:
                k = t_args(a)
                ex = terminal.start(a["command"], root_id=k["root_id"], cwd=k["cwd"], shell=k["shell"],
                                    timeout=k["timeout"], approval_id=approval_id)
                ready = a.get("ready_pattern")
                if ready:
                    pattern = re.compile(ready)
                    deadline = time.monotonic() + float(a.get("ready_timeout_seconds", 30))
                    while time.monotonic() < deadline:
                        out = terminal.live_output(ex.execution_id)
                        if pattern.search(out["stdout"] + out["stderr"]):
                            return Outcome(ok=True, verified=True, data={
                                "execution_id": ex.execution_id, "ready": True, "still_running": True},
                                note="Ready; the process keeps running and can be stopped with its execution_id.")
                        if ex._done.is_set():
                            break
                        time.sleep(0.1)
                    terminal.cancel(ex.execution_id) if not ex._done.is_set() else None
                    terminal.wait(ex.execution_id, 5)
                    return Outcome(ok=False, verified=False, data={"execution_id": ex.execution_id},
                                   error="The command did not report readiness in time"
                                   if ex.status in {"cancelled", "running"} else
                                   f"The command ended ({ex.status}) before reporting readiness")
                terminal.wait(ex.execution_id, float(ex.timeout) + 5)
                pub = ex.public()
                data = {"execution_id": ex.execution_id, "status": ex.status, "exit_code": ex.exit_code,
                        "stdout": pub["stdout"][:4000], "stderr": pub["stderr"][:4000]}
                if ex.status != "completed":
                    return Outcome(ok=False, data=data,
                                   error=f"Command {ex.status} (exit code {ex.exit_code})")
                expect = a.get("expect_output")
                verified = None if not expect else bool(re.search(expect, pub["stdout"]))
                return Outcome(ok=True, verified=verified, data=data,
                               note=None if expect else
                               "Exit code 0 only shows the command ended normally.")
            return _guarded(go)
        add(StepHandler("terminal.run", t_inspect, t_run, t_request, required=("command", "root_id")))

    # ---- browser (async) ----------------------------------------------------------------------
    if browser is not None:
        def ainspect(summary: Callable[[dict], str], risk: str = "low") -> Callable:
            async def inspect(a: dict) -> Inspection:
                return Inspection(summary(a), risk)
            return inspect

        def abrowser(cap: str, required: tuple[str, ...], call: Callable, summary: Callable,
                     idempotent: bool = False) -> None:
            async def run(a: dict, _approval: str | None) -> Outcome:
                async def go() -> Outcome:
                    data = await call(a)
                    data = data if isinstance(data, dict) else {"items": data}
                    return Outcome(ok=True, verified=data.get("verified", True), data=data,
                                   note=data.get("note"))
                return await _aguarded(go)
            add(StepHandler(cap, ainspect(summary), run, None, idempotent=idempotent,
                            required=required, is_async=True))

        abrowser("browser.navigate", ("url",), lambda a: browser.navigate(a["url"]),
                 lambda a: f"Open {a['url']}", idempotent=True)
        abrowser("browser.observe", (), lambda a: browser.observe(), lambda a: "Read the current page",
                 idempotent=True)
        abrowser("browser.extract", (), lambda a: browser.extract(),
                 lambda a: "Extract headings, tables and lists", idempotent=True)
        abrowser("browser.find", ("query",), lambda a: browser.find(a["query"]),
                 lambda a: f"Find '{a['query']}' on the page", idempotent=True)
        abrowser("browser.fill", ("ref", "text"), lambda a: browser.fill(a["ref"], a["text"]),
                 lambda a: f"Fill field {a['ref']}")
        abrowser("browser.select", ("ref", "value"), lambda a: browser.select_option(a["ref"], a["value"]),
                 lambda a: f"Choose '{a['value']}' in {a['ref']}")
        abrowser("browser.check", ("ref", "checked"),
                 lambda a: browser.set_checked(a["ref"], bool(a["checked"])),
                 lambda a: f"Set checkbox {a['ref']}")
        abrowser("browser.scroll", (), lambda a: browser.scroll(a.get("direction", "down"),
                                                                 int(a.get("amount", 600))),
                 lambda a: "Scroll the page")
        if browser_actions is not None:
            for kind, cap in (("click", "browser.click_ref"), ("download", "browser.download"),
                              ("upload", "browser.upload")):
                def mk(kind: str, cap: str) -> None:
                    async def inspect(a: dict) -> Inspection:
                        _, summary, _ = await browser_actions._plan(
                            kind, a["ref"], a.get("root_id"), a.get("relative_path"))
                        return Inspection(summary, "high" if "CONSEQUENTIAL" in summary else "medium", True)

                    async def request(a: dict) -> str:
                        return (await browser_actions.preview(
                            kind, a["ref"], root_id=a.get("root_id"),
                            relative_path=a.get("relative_path")))["approval_id"]

                    async def run(a: dict, approval_id: str | None) -> Outcome:
                        async def go() -> Outcome:
                            r = await browser_actions.execute(
                                approval_id, kind, a["ref"], root_id=a.get("root_id"),
                                relative_path=a.get("relative_path"))
                            return Outcome(ok=True, verified=bool(r.get("verified")), data=r,
                                           note=r.get("note"))
                        return await _aguarded(go)
                    add(StepHandler(cap, inspect, run, request, required=("ref",), is_async=True))
                mk(kind, cap)

    # ---- desktop (Windows) ----------------------------------------------------------------------
    if desktop is not None and desktop_adapter is not None and os_family == "windows":
        add(_read_only("desktop.observe", ("window_handle",),
                       lambda a: desktop.observe(int(a["window_handle"])),
                       lambda a: f"Read controls of window {a['window_handle']}"))
        for action in ("click", "toggle", "select", "keys", "scroll", "click_at", "screenshot"):
            def mkd(action: str) -> None:
                def ident(a: dict) -> dict:
                    return {"automation_id": a.get("automation_id", ""),
                            "control_type": a.get("control_type", ""), "name": a.get("name", ""),
                            "params": a.get("params") or {}}

                def inspect(a: dict) -> Inspection:
                    _, _, info, payload = desktop._plan(
                        action, int(a["window_handle"]), None, a.get("automation_id", ""),
                        a.get("control_type", ""), a.get("name", ""), a.get("params") or {})
                    label = (info["name"] or info["automation_id"]) if info else "window"
                    return Inspection(f"{action} '{label}' in '{payload['window_title'][:60]}'",
                                      "high" if action in {"keys", "click_at", "screenshot"} else "medium",
                                      True)

                def request(a: dict) -> str:
                    return desktop.preview(action, int(a["window_handle"]), **ident(a))["approval_id"]

                def run(a: dict, approval_id: str | None) -> Outcome:
                    def go() -> Outcome:
                        window = desktop_adapter._find_window(int(a["window_handle"]))
                        title = str(window.window_text() or "").strip()
                        r = desktop.execute(approval_id, action, int(a["window_handle"]), title,
                                            **ident(a))
                        return Outcome(ok=True, verified=bool(r.get("verified")), data=r,
                                       note=r.get("note"))
                    return _guarded(go)
                add(StepHandler(f"desktop.{action}", inspect, run, request, required=("window_handle",)))
            mkd(action)
    return h
