import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mia.computer.app_discovery import ApplicationDiscovery
from mia.computer.applications import (
    ApplicationDefinition,
    ApplicationLauncher,
    ApplicationLaunchError,
)
from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.processes import ProcessAccessError, ProcessManager
from mia.computer.windows_ui import DesktopAccessError, WindowsUIAdapter, looks_unsaved
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture
def approvals(tmp_path):
    return ApprovalStore(tmp_path / "a.sqlite3")


@pytest.fixture
def child():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield proc
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def test_real_system_status_top_and_info(approvals):
    pm = ProcessManager(approvals, sample_interval=0.05)
    status = pm.system_status()
    assert 0 <= status["cpu_percent"] <= 100 and status["memory_total_bytes"] > 0
    assert status["disks"] and "no outbound" in status["network_note"]
    top = pm.top(by="memory", limit=5)
    assert 1 <= len(top) <= 5 and top == sorted(top, key=lambda r: -r["memory_rss_bytes"])
    assert pm.top(by="cpu", limit=3)
    with pytest.raises(ValueError):
        pm.top(by="disk")
    me = pm.info(os.getpid())
    assert me["terminable"] is False and "Mia" in me["protected_reason"]
    assert "cmdline" not in me and "environ" not in me


def test_real_terminate_with_approval_and_verification(approvals, child):
    pm = ProcessManager(approvals, sample_interval=0.05)
    assert pm.info(child.pid)["terminable"] is True
    with pytest.raises(ApprovalError):
        pm.execute_terminate("0" * 32, child.pid)  # no approval at all
    preview = pm.preview_terminate(child.pid)
    with pytest.raises(ApprovalError):
        pm.execute_terminate(preview["approval_id"], child.pid)  # not yet approved
    assert child.poll() is None
    approvals.approve(preview["approval_id"])
    # approval for a graceful stop cannot authorize a force-kill
    with pytest.raises(ApprovalError):
        pm.execute_terminate(preview["approval_id"], child.pid, force=True)
    preview2 = pm.preview_terminate(child.pid)
    approvals.approve(preview2["approval_id"])
    result = pm.execute_terminate(preview2["approval_id"], child.pid)
    assert result["verified"] is True and result["status"] == "stopped"
    with pytest.raises((ApprovalError, ProcessAccessError)):  # gone, and approval is one-time
        pm.execute_terminate(preview2["approval_id"], child.pid)


def test_refuses_own_parent_and_invalid_pids(approvals):
    pm = ProcessManager(approvals)
    for pid in (os.getpid(), os.getppid()):
        with pytest.raises(ProcessAccessError):
            pm.preview_terminate(pid)
    with pytest.raises(ValueError):
        pm.preview_terminate(0)
    with pytest.raises(ProcessAccessError):
        pm.preview_terminate(4_000_000)


class FakeProc:
    def __init__(self, pid, name, user="owner", created=1000.0):
        self.pid, self._name, self._user, self.created = pid, name, user, created
        self.killed = self.terminated = False

    def name(self):
        return self._name

    def username(self):
        return self._user

    def create_time(self):
        return self.created

    def parents(self):
        return []

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0

    def is_running(self):
        return False

    def status(self):
        return "dead"


def fake_psutil(procs):
    class NoSuchProcess(Exception):
        pass

    class AccessDenied(Exception):
        pass

    class TimeoutExpired(Exception):
        pass

    def Process(pid):  # noqa: N802
        if pid not in procs:
            raise NoSuchProcess
        return procs[pid]

    return SimpleNamespace(Process=Process, NoSuchProcess=NoSuchProcess,
                           AccessDenied=AccessDenied, TimeoutExpired=TimeoutExpired)


def test_protected_and_foreign_processes_refused(approvals):
    procs = {
        100: FakeProc(100, "notepad.exe"),
        101: FakeProc(101, "LSASS.EXE"),
        102: FakeProc(102, "Svchost.exe"),
        103: FakeProc(103, "tool.exe", user="DOMAIN\\someoneelse"),
        104: FakeProc(104, "MsMpEng.exe"),
        4: FakeProc(4, "System"),
    }
    pm = ProcessManager(approvals, psutil_module=fake_psutil(procs), own_pid=999,
                        current_user="owner")
    assert pm.preview_terminate(100)["target"]["name"] == "notepad.exe"
    for pid in (101, 102, 103, 104, 4):
        with pytest.raises(ProcessAccessError):
            pm.preview_terminate(pid)
    procs[105] = FakeProc(105, "app.exe", user="DESKTOP\\Owner")
    assert pm.preview_terminate(105)  # domain prefix and case ignored for the same user


def test_pid_reuse_invalidates_approval(approvals):
    procs = {100: FakeProc(100, "notepad.exe")}
    pm = ProcessManager(approvals, psutil_module=fake_psutil(procs), own_pid=999,
                        current_user="owner")
    preview = pm.preview_terminate(100)
    approvals.approve(preview["approval_id"])
    procs[100] = FakeProc(100, "notepad.exe", created=5000.0)  # new process reused the PID
    with pytest.raises(ApprovalError):
        pm.execute_terminate(preview["approval_id"], 100)
    assert not procs[100].terminated
    procs[100] = FakeProc(100, "other.exe")  # different program, same PID and time
    with pytest.raises(ApprovalError):
        pm.execute_terminate(preview["approval_id"], 100)


def test_app_discovery_windows_linux_macos(tmp_path):
    start = tmp_path / "Start Menu"
    (start / "Sub").mkdir(parents=True)
    (start / "Sub" / "Visual Studio Code.lnk").write_text("")
    (start / "Uninstall Foo.lnk").write_text("")

    class FakeWinreg:
        HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = "HKLM", "HKCU"
        data = {
            ("HKLM", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"): {
                "a": {"DisplayName": "Git", "Publisher": "Git Dev", "DisplayVersion": "2.4"},
                "b": {"DisplayName": "Hidden Component", "SystemComponent": 1},
                "c": {"DisplayName": "Visual Studio Code"},
                "d": {},
            }
        }

        def OpenKey(self, parent, name):  # noqa: N802
            if isinstance(parent, str):
                if (parent, name) in self.data:
                    return ("root", self.data[(parent, name)])
                raise OSError
            return ("sub", parent[1][name])

        def EnumKey(self, key, index):  # noqa: N802
            keys = list(key[1])
            if index >= len(keys):
                raise OSError
            return keys[index]

        def QueryValueEx(self, key, name):  # noqa: N802
            if name not in key[1]:
                raise OSError
            return (key[1][name], 1)

    win = ApplicationDiscovery(os_family="windows", winreg_module=FakeWinreg(),
                               extra_dirs={"windows": [start]})
    names = [a["name"] for a in win.discover()]
    assert names == ["Git", "Visual Studio Code"]  # deduped, hidden/uninstall skipped
    assert win.search("git")[0]["publisher"] == "Git Dev"
    with pytest.raises(ValueError):
        win.search(" ")

    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / "a.desktop").write_text("[Desktop Entry]\nName=Firefox\nExec=firefox %u\n")
    (apps / "b.desktop").write_text("[Desktop Entry]\nName=Secret Helper\nNoDisplay=true\n")
    lin = ApplicationDiscovery(os_family="linux", extra_dirs={"linux": [apps]})
    assert [a["name"] for a in lin.discover()] == ["Firefox"]
    assert "exec" not in str(lin.discover()).lower()

    mac_dir = tmp_path / "Applications"
    (mac_dir / "Safari.app").mkdir(parents=True)
    mac = ApplicationDiscovery(os_family="macos", extra_dirs={"macos": [mac_dir]})
    assert [a["name"] for a in mac.discover()] == ["Safari"]


def test_launch_failure_and_success_detection(approvals, monkeypatch):
    launcher = ApplicationLauncher([ApplicationDefinition("ed", "Editor", "ed.exe")], approvals,
                                   launch_check_seconds=0)
    monkeypatch.setattr(ApplicationLauncher, "_resolve_executable",
                        staticmethod(lambda e: "C:/ed.exe"))

    def run(poll_value, raises=None):
        def popen(*a, **k):
            if raises:
                raise raises
            return SimpleNamespace(pid=77, poll=lambda: poll_value)
        monkeypatch.setattr("mia.computer.applications.subprocess.Popen", popen)
        preview = launcher.preview_launch("ed")
        approvals.approve(preview["approval_id"])
        return launcher.execute_launch("ed", preview["approval_id"])

    ok = run(None)
    assert ok["verified"] is True and ok["launch_state"] == "running"
    handoff = run(0)
    assert handoff["verified"] is False and "handed_off" in handoff["launch_state"]
    with pytest.raises(ApplicationLaunchError, match="exited immediately"):
        run(3)
    with pytest.raises(ApplicationLaunchError, match="could not be started"):
        run(None, raises=FileNotFoundError(2, "missing"))


class FakeWin:
    def __init__(self, handle=11, title="Notes", closes=True):
        self.handle, self._title, self.pid = handle, title, 500
        self.element_info = SimpleNamespace(class_name="NotesWnd")
        self.state, self.focused, self.closes = "normal", False, closes
        self.open = True

    def window_text(self):
        return self._title

    def process_id(self):
        return self.pid

    def set_focus(self):
        self.focused = True
        self.state = "normal"

    def minimize(self):
        self.state = "minimized"

    def maximize(self):
        self.state = "maximized"

    def restore(self):
        self.state = "normal"

    def close(self):
        if self.closes:
            self.open = False

    def is_minimized(self):
        return self.state == "minimized"

    def is_maximized(self):
        return self.state == "maximized"

    def has_keyboard_focus(self):
        return self.focused


class FakeDesktop:
    def __init__(self, *wins):
        self.wins = list(wins)

    def windows(self, visible_only=True):
        return [w for w in self.wins if w.open]


def adapter(approvals, *wins):
    return WindowsUIAdapter(approvals, desktop_factory=lambda: FakeDesktop(*wins),
                            platform_name="win32")


def do(adapter_, approvals, win, action, **kw):
    preview = adapter_.preview_window_action(win.handle, action, **kw)
    approvals.approve(preview["approval_id"])
    return adapter_.execute_window_action(
        preview["approval_id"], win.handle, win._title, action, wait=lambda s: None, **kw)


def test_window_actions_verified_by_observation(approvals):
    win = FakeWin()
    a = adapter(approvals, win)
    r0 = do(a, approvals, win, "minimize")
    assert r0["verified"] and r0["state"] == "minimized"
    r = do(a, approvals, win, "maximize")
    assert r["verified"] and r["state"] == "maximized"
    assert do(a, approvals, win, "restore")["verified"]
    assert do(a, approvals, win, "focus")["verified"] and win.focused
    closed = do(a, approvals, win, "close")
    assert closed["verified"] and closed["state"] == "gone"


def test_window_action_not_verified_when_nothing_changes(approvals):
    win = FakeWin(closes=False)
    a = adapter(approvals, win)
    result = do(a, approvals, win, "close")
    assert result["verified"] is False and "still open" in result["note"]

    class Stubborn(FakeWin):
        def maximize(self):  # returns without exception but changes nothing
            pass

    stub = Stubborn(handle=12)
    a2 = adapter(approvals, stub)
    assert do(a2, approvals, stub, "maximize")["verified"] is False


def test_close_with_unsaved_marker_needs_acknowledgement(approvals):
    assert looks_unsaved("*notes.txt - Editor") and looks_unsaved("main.py (modified)")
    assert not looks_unsaved("Calculator")
    win = FakeWin(title="*draft.txt - Editor")
    a = adapter(approvals, win)
    with pytest.raises(DesktopAccessError, match="unsaved"):
        a.preview_window_action(win.handle, "close")
    assert do(a, approvals, win, "close", acknowledge_unsaved_risk=True)["verified"]


def test_window_action_approval_binding(approvals):
    win = FakeWin()
    a = adapter(approvals, win)
    preview = a.preview_window_action(win.handle, "minimize")
    approvals.approve(preview["approval_id"])
    with pytest.raises(ApprovalError):  # approved minimize cannot close
        a.execute_window_action(preview["approval_id"], win.handle, "Notes", "close",
                                wait=lambda s: None)
    win._title = "Changed"
    with pytest.raises(DesktopAccessError):
        a.execute_window_action(preview["approval_id"], win.handle, "Notes", "minimize",
                                wait=lambda s: None)
    with pytest.raises(ValueError):
        a.preview_window_action(win.handle, "kill")
    with pytest.raises(DesktopAccessError):
        a.preview_window_action(999, "focus")
    with pytest.raises(RuntimeError):
        WindowsUIAdapter(approvals, platform_name="linux").preview_window_action(1, "focus")


def test_hung_window_detection(approvals):
    w1, w2 = FakeWin(11, "Fine"), FakeWin(12, "Stuck")
    a = adapter(approvals, w1, w2)
    hung = a.detect_hung_windows(hung_checker=lambda handle: handle == 12)
    assert [w["title"] for w in hung] == ["Stuck"]


def test_process_and_window_api(tmp_path, child):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    win = FakeWin()
    client = TestClient(create_app(
        store=ObservatoryStore(tmp_path / "o.sqlite3"), approvals=approvals,
        windows_ui=adapter(approvals, win),
        process_manager=ProcessManager(approvals, sample_interval=0.05),
        app_discovery=ApplicationDiscovery(os_family="linux", extra_dirs={"linux": []}),
    ))
    assert client.get("/api/computer/system/status").json()["memory_total_bytes"] > 0
    assert client.get("/api/computer/processes/top?by=memory&limit=3").status_code == 200
    assert client.get("/api/computer/processes/top?by=bogus").status_code == 422
    assert client.get(f"/api/computer/processes/{child.pid}").json()["terminable"] is True
    assert client.get("/api/computer/processes/4000000").status_code == 403
    assert client.get("/api/computer/apps/discover").json()["items"] == []

    body = {"pid": child.pid, "force": False}
    p = client.post("/api/computer/processes/terminate/preview", json=body)
    assert p.status_code == 201
    aid = p.json()["approval_id"]
    assert client.post("/api/computer/processes/terminate/execute",
                       json={**body, "approval_id": aid}).status_code == 409
    assert child.poll() is None
    client.post(f"/api/computer/approvals/{aid}/approve")
    done = client.post("/api/computer/processes/terminate/execute",
                       json={**body, "approval_id": aid})
    assert done.status_code == 200 and done.json()["verified"] is True
    own = client.post("/api/computer/processes/terminate/preview", json={"pid": os.getpid()})
    assert own.status_code == 403

    wbody = {"window_handle": win.handle, "action": "maximize"}
    wp = client.post("/api/desktop/windows/action/preview", json=wbody)
    assert wp.status_code == 201
    waid = wp.json()["approval_id"]
    client.post(f"/api/computer/approvals/{waid}/approve")
    wr = client.post("/api/desktop/windows/action/execute",
                     json={**wbody, "approval_id": waid, "window_title": "Notes"})
    assert wr.status_code == 200 and wr.json()["state"] == "maximized"
    assert client.post("/api/desktop/windows/action/preview",
                       json={**wbody, "action": "explode"}).status_code == 422
    assert Path(tmp_path).exists()
