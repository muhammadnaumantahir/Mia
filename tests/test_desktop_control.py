"""Desktop control logic against a fake UI Automation tree.

These tests prove the governance, binding and verification logic. They do NOT prove behavior of the
real pywinauto/UIA backend; that must be verified on a Windows PC.
"""

import os
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.control import AutomationControl, AutomationStopped
from mia.computer.desktop_control import (
    DesktopController,
    keys_to_send,
    make_window_guard,
)
from mia.computer.windows_ui import DesktopAccessError, WindowsUIAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FControl:
    def __init__(self, aid, ctype, name="", enabled=True, toggle=None, selected=None,
                 value=None, on_act=None, password=False, sticky=False):
        self.element_info = SimpleNamespace(automation_id=aid, control_type=ctype, name=name,
                                            class_name=ctype, is_password=password)
        self.enabled, self.toggle_state, self.selected = enabled, toggle, selected
        self.value, self.on_act, self.sticky = value, on_act, sticky
        self.scrolled = []

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.enabled

    def get_toggle_state(self):
        if self.toggle_state is None:
            raise NotImplementedError
        return self.toggle_state

    def is_selected(self):
        if self.selected is None:
            raise NotImplementedError
        return self.selected

    def get_value(self):
        if self.value is None:
            raise NotImplementedError
        return self.value

    def set_edit_text(self, text):
        if not self.sticky:
            self.value = text

    def set_focus(self):
        pass

    def _act(self):
        if self.on_act:
            self.on_act(self)

    invoke = click_input = _act

    def toggle(self):
        if not self.sticky:
            self.toggle_state = 1 - self.toggle_state

    def select(self, value=None):
        if not self.sticky:
            self.selected = True
            if value is not None:
                self.value = value

    def scroll(self, direction, amount, count):
        self.scrolled.append((direction, amount, count))
        if self.on_act:
            self.on_act(self)


class FWin:
    def __init__(self, handle, title, controls, pid=900):
        self.handle, self._title, self.pid, self.controls = handle, title, pid, controls
        self.element_info = SimpleNamespace(class_name="AppWnd")
        self.focused = False
        self.rect = SimpleNamespace(left=100, top=50, right=500, bottom=350)
        self.open = True

    def window_text(self):
        return self._title

    def process_id(self):
        return self.pid

    def descendants(self):
        return list(self.controls)

    def set_focus(self):
        self.focused = True

    def rectangle(self):
        return self.rect

    def capture_as_image(self):
        return SimpleNamespace(save=lambda path: open(path, "wb").write(b"\x89PNG-fake"))

    def child_window(self, auto_id=None, control_type=None):
        hits = [c for c in self.controls if c.element_info.automation_id == auto_id
                and c.element_info.control_type == control_type]
        if len(hits) != 1:
            raise LookupError("not exactly one")
        return SimpleNamespace(wrapper_object=lambda: hits[0])


class FDesktop:
    def __init__(self, *wins):
        self.wins = list(wins)

    def windows(self, visible_only=True):
        return [w for w in self.wins if w.open]


class Keyboard:
    def __init__(self, on_send=None):
        self.sent, self.on_send = [], on_send

    def __call__(self, text, **kw):
        self.sent.append(text)
        if self.on_send:
            self.on_send(text)


class Mouse:
    def __init__(self, on_click=None):
        self.clicks, self.on_click = [], on_click

    def click(self, button="left", coords=(0, 0)):
        self.clicks.append((button, coords))
        if self.on_click:
            self.on_click()


@pytest.fixture
def rig(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    desktop = FDesktop()
    adapter = WindowsUIAdapter(approvals, desktop_factory=lambda: desktop, platform_name="win32")
    control = AutomationControl()
    keyboard, mouse = Keyboard(), Mouse()
    ctl = DesktopController(adapter, approvals, control=control, keyboard=keyboard, mouse=mouse,
                            screenshot_dir=tmp_path / "shots", sleep=lambda s: None)
    return SimpleNamespace(approvals=approvals, desktop=desktop, adapter=adapter, ctl=ctl,
                           control=control, keyboard=keyboard, mouse=mouse)


def go(rig, action, win, **kw):
    pv = rig.ctl.preview(action, win.handle, **kw)
    rig.approvals.approve(pv["approval_id"])
    return rig.ctl.execute(pv["approval_id"], action, win.handle, win._title, **kw)


def notepad(rig, **extra):
    win = FWin(11, "Notes", [])
    save = FControl("btnSave", "Button", "Save", on_act=lambda c: win.controls.append(
        FControl("lblSaved", "Text", "Saved!")))
    noop = FControl("btnNoop", "Button", "Do nothing")
    delete = FControl("btnDel", "Button", "Delete everything")
    check = FControl("chkWrap", "CheckBox", "Word wrap", toggle=0)
    edit = FControl("txtBody", "Edit", "Body", value="secret-body-text")
    pw = FControl("txtPw", "Edit", "Password", value="hunter2", password=True)
    item = FControl("itemA", "ListItem", "Item A", selected=False)
    win.controls.extend([save, noop, delete, check, edit, pw, item])
    rig.desktop.wins.append(win)
    return win, SimpleNamespace(save=save, noop=noop, delete=delete, check=check, edit=edit,
                                item=item)


def test_observe_returns_semantics_not_values_or_passwords(rig):
    win, _ = notepad(rig)
    result = rig.ctl.observe(win.handle)
    ids = {c["automation_id"]: c for c in result["controls"]}
    assert "txtPw" not in ids and "hunter2" not in str(result)
    assert "secret-body-text" not in str(result)  # edit values are never returned
    assert ids["btnSave"]["actions"] == ["click"] and ids["btnSave"]["consequential"] is False
    assert ids["btnDel"]["consequential"] is True
    assert "toggle" in ids["chkWrap"]["actions"] and "keys" in ids["txtBody"]["actions"]
    assert result["content_trust"].startswith("local_ui_labels")
    with pytest.raises(ValueError):
        rig.ctl.observe(win.handle, limit=0)
    with pytest.raises(DesktopAccessError):
        rig.ctl.observe(999)


def test_click_verified_by_reobserving_and_unverified_when_nothing_changes(rig):
    win, c = notepad(rig)
    r = go(rig, "click", win, automation_id="btnSave", control_type="Button", name="Save")
    assert r["verified"] is True and r["effects"]["content_changed"] and r["effects"]["controls_after"] > r["effects"]["controls_before"]
    r = go(rig, "click", win, automation_id="btnNoop", control_type="Button", name="Do nothing")
    assert r["verified"] is False and "no change" in r["note"]


def test_click_that_opens_a_dialog_or_closes_the_window(rig):
    win, c = notepad(rig)
    dialog = FWin(12, "Confirm", [FControl("ok", "Button", "OK")])
    c.noop.on_act = lambda _c: rig.desktop.wins.append(dialog)
    r = go(rig, "click", win, automation_id="btnNoop", control_type="Button", name="Do nothing")
    assert r["verified"] and r["effects"]["new_windows"] == 1

    def close(_c):
        win.open = False
    c.save.on_act = close
    r = go(rig, "click", win, automation_id="btnSave", control_type="Button", name="Save")
    assert r["verified"] and r["effects"]["window_closed"] is True


def test_approval_is_bound_to_window_control_and_action(rig):
    win, c = notepad(rig)
    ident = {"automation_id": "btnSave", "control_type": "Button", "name": "Save"}
    pv = rig.ctl.preview("click", win.handle, **ident)
    assert "Save" in pv["summary"] and not pv["summary"].startswith("CONSEQUENTIAL")
    with pytest.raises(ApprovalError):  # not approved
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **ident)
    rig.approvals.approve(pv["approval_id"])
    other = {"automation_id": "btnNoop", "control_type": "Button", "name": "Do nothing"}
    with pytest.raises(ApprovalError):  # different control
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **other)
    with pytest.raises(ApprovalError):  # different action
        rig.ctl.execute(pv["approval_id"], "toggle", win.handle, "Notes",
                        automation_id="chkWrap", control_type="CheckBox", name="Word wrap")
    c.save.enabled = False  # target state changed after approval
    with pytest.raises(DesktopAccessError, match="disabled"):
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **ident)
    c.save.enabled = True
    win._title = "Notes - other document"
    with pytest.raises(DesktopAccessError, match="title"):
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **ident)
    win._title = "Notes"
    rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **ident)
    with pytest.raises(ApprovalError):  # one time
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", **ident)


def test_consequential_controls_are_flagged_and_ambiguity_refused(rig):
    win, c = notepad(rig)
    pv = rig.ctl.preview("click", win.handle, automation_id="btnDel", control_type="Button",
                         name="Delete everything")
    assert pv["summary"].startswith("CONSEQUENTIAL")
    win.controls.append(FControl("btnSave", "Button", "Save"))  # duplicate identity
    with pytest.raises(DesktopAccessError, match="uniquely"):
        rig.ctl.preview("click", win.handle, automation_id="btnSave", control_type="Button",
                        name="Save")
    with pytest.raises(DesktopAccessError, match="not found"):
        rig.ctl.preview("click", win.handle, automation_id="nope", control_type="Button")
    with pytest.raises(ValueError):
        rig.ctl.preview("click", win.handle, control_type="Button")
    with pytest.raises(DesktopAccessError, match="does not support"):
        rig.ctl.preview("toggle", win.handle, automation_id="btnNoop", control_type="Button",
                        name="Do nothing")
    with pytest.raises(ValueError):
        rig.ctl.preview("format_disk", win.handle)


def test_toggle_and_select_verified_from_read_back(rig):
    win, c = notepad(rig)
    chk = {"automation_id": "chkWrap", "control_type": "CheckBox", "name": "Word wrap"}
    r = go(rig, "toggle", win, **chk)
    assert r["verified"] and c.check.toggle_state == 1
    c.check.sticky = True  # control ignores the toggle
    r = go(rig, "toggle", win, **chk)
    assert r["verified"] is False and "did not change" in r["note"]
    item = {"automation_id": "itemA", "control_type": "ListItem", "name": "Item A"}
    assert go(rig, "select", win, **item)["verified"] is True
    combo = FControl("cmb", "ComboBox", "Size", value="Small")
    combo.on_act = None
    win.controls.append(combo)
    cid = {"automation_id": "cmb", "control_type": "ComboBox", "name": "Size"}
    with pytest.raises(ValueError):
        rig.ctl.preview("select", win.handle, **cid)  # needs a value
    r = go(rig, "select", win, params={"value": "Large"}, **cid)
    assert combo.value == "Large" and r["verified"]


def test_key_validation_and_conversion():
    assert keys_to_send([{"text": "a+b (x)"}, {"key": "enter"}])[0] == "a{+}b{SPACE}{(}x{)}{ENTER}"
    assert keys_to_send([{"key": "ctrl+a"}])[0] == "^a"
    for bad in ([{"key": "ALT+F4"}], [{"key": "WIN"}], [{"key": "CTRL+ALT+DELETE"}],
                [{"key": "CTRL+V"}]):
        with pytest.raises(DesktopAccessError):
            keys_to_send(bad)
    with pytest.raises(DesktopAccessError):
        keys_to_send([{"text": "AKIAABCDEFGHIJKLMNOP"}])
    for bad in ([], "x", [{"text": ""}], [{"text": "a", "key": "TAB"}], [{"other": "x"}],
                [{"text": "x" * 501}], [{"text": "y" * 500}] * 3):
        with pytest.raises(ValueError):
            keys_to_send(bad)


def test_keys_sent_to_control_and_verified_by_value_change(rig):
    win, c = notepad(rig)
    ident = {"automation_id": "txtBody", "control_type": "Edit", "name": "Body"}
    rig.keyboard.on_send = lambda text: setattr(c.edit, "value", c.edit.value + "!")
    r = go(rig, "keys", win, params={"keys": [{"text": "hi there"}, {"key": "ENTER"}]}, **ident)
    assert rig.keyboard.sent == ["hi{SPACE}there{ENTER}"] and r["verified"] is True
    rig.keyboard.on_send = None  # keystrokes had no visible effect
    r = go(rig, "keys", win, params={"keys": [{"key": "TAB"}]}, **ident)
    assert r["verified"] is False and "no change" in r["note"]
    with pytest.raises(DesktopAccessError):
        rig.ctl.preview("keys", win.handle, params={"keys": [{"key": "ALT+F4"}]}, **ident)


def test_scroll_and_coordinate_fallback(rig):
    win, c = notepad(rig)
    pane = FControl("pane1", "Pane", "Content")
    pane.on_act = lambda ctl: win.controls.append(FControl(f"row{len(win.controls)}", "Text", "row"))
    win.controls.append(pane)
    ident = {"automation_id": "pane1", "control_type": "Pane", "name": "Content"}
    r = go(rig, "scroll", win, params={"direction": "down", "pages": 2}, **ident)
    assert pane.scrolled == [("down", "page", 2)] and r["verified"]
    for bad in ({"direction": "left"}, {"pages": 0}, {"pages": 21}):
        with pytest.raises(ValueError):
            rig.ctl.preview("scroll", win.handle, params=bad, **ident)

    pv = rig.ctl.preview("click_at", win.handle, params={"x": 10, "y": 20})
    assert pv["summary"].startswith("CONSEQUENTIAL") and "fallback" in pv["summary"]
    for bad in ({"x": -1, "y": 5}, {"x": 400, "y": 5}, {"x": 5, "y": 300}, {"x": "a", "y": 1},
                {"x": True, "y": 1}):
        with pytest.raises(ValueError):
            rig.ctl.preview("click_at", win.handle, params=bad)
    rig.mouse.on_click = lambda: setattr(win, "_title", "Notes*")
    rig.approvals.approve(pv["approval_id"])
    r = rig.ctl.execute(pv["approval_id"], "click_at", win.handle, "Notes", params={"x": 10, "y": 20})
    assert rig.mouse.clicks == [("left", (110, 70))] and r["verified"]


def test_screenshot_needs_approval_and_is_never_automatic(rig, tmp_path):
    win, _ = notepad(rig)
    pv = rig.ctl.preview("screenshot", win.handle)
    assert pv["summary"].startswith("CONSEQUENTIAL") and "captures" in pv["summary"]
    with pytest.raises(ApprovalError):
        rig.ctl.execute(pv["approval_id"], "screenshot", win.handle, "Notes")
    rig.approvals.approve(pv["approval_id"])
    r = rig.ctl.execute(pv["approval_id"], "screenshot", win.handle, "Notes")
    assert r["verified"] and open(r["path"], "rb").read().startswith(b"\x89PNG")
    assert not list((tmp_path / "shots").glob("*")) == [] and len(list((tmp_path / "shots").iterdir())) == 1
    bare = DesktopController(rig.adapter, rig.approvals)
    with pytest.raises(DesktopAccessError, match="not configured"):
        bare.preview("screenshot", win.handle)


def test_security_and_admin_windows_are_never_driven(rig):
    names = {901: "consent.exe", 902: "notepad.exe", 903: "REGEDIT.EXE"}
    rig.adapter.window_guard = make_window_guard(lambda pid: names.get(pid))
    uac = FWin(21, "User Account Control", [FControl("yes", "Button", "Yes")], pid=901)
    ok = FWin(22, "Notepad", [FControl("b", "Button", "Bold")], pid=902)
    reg = FWin(23, "Registry Editor", [FControl("b", "Button", "x")], pid=903)
    unknown = FWin(24, "Mystery", [FControl("b", "Button", "x")], pid=999)
    rig.desktop.wins.extend([uac, ok, reg, unknown])
    for win in (uac, reg):
        with pytest.raises(DesktopAccessError, match="security or administration"):
            rig.ctl.observe(win.handle)
        with pytest.raises(DesktopAccessError):
            rig.ctl.preview("click", win.handle, automation_id="yes", control_type="Button",
                            name="Yes")
    assert rig.ctl.observe(ok.handle)["controls"]
    assert rig.ctl.observe(unknown.handle)["controls"]  # unresolvable name is allowed through
    with pytest.raises(DesktopAccessError):  # window management and typing share the guard
        rig.adapter.preview_window_action(uac.handle, "close")


def test_activity_is_visible_and_stop_switch_blocks_everything(rig):
    win, c = notepad(rig)
    seen = {}
    c.save.on_act = lambda _c: seen.update(rig.control.status())
    go(rig, "click", win, automation_id="btnSave", control_type="Button", name="Save")
    assert seen["state"] == "active" and seen["activities"][0]["kind"] == "desktop.click"
    assert "Save" in seen["activities"][0]["description"]
    assert rig.control.status()["state"] == "idle"

    pv = rig.ctl.preview("click", win.handle, automation_id="btnNoop", control_type="Button",
                         name="Do nothing")
    rig.approvals.approve(pv["approval_id"])
    result = rig.control.request_stop()
    assert result["stopped_at"] and rig.control.status()["state"] == "stopped"
    with pytest.raises(AutomationStopped):
        rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", automation_id="btnNoop",
                        control_type="Button", name="Do nothing")
    with pytest.raises(AutomationStopped):
        rig.ctl.observe(win.handle)
    rig.control.resume()
    rig.ctl.execute(pv["approval_id"], "click", win.handle, "Notes", automation_id="btnNoop",
                    control_type="Button", name="Do nothing")  # approval was not consumed by the stop


def test_type_text_now_reads_back_and_reports_failure(rig):
    win, c = notepad(rig)
    kw = dict(window_handle=win.handle, window_title="Notes", automation_id="txtBody",
              control_type="Edit", text="hello")
    pv = rig.adapter.preview_type_text(**kw)
    rig.approvals.approve(pv["approval_id"])
    r = rig.adapter.execute_type_text(approval_id=pv["approval_id"], process_id=900,
                                      window_class="AppWnd", **kw)
    assert r["verified"] is True and c.edit.value == "hello"
    c.edit.sticky = True
    pv = rig.adapter.preview_type_text(**{**kw, "text": "again"})
    rig.approvals.approve(pv["approval_id"])
    r = rig.adapter.execute_type_text(approval_id=pv["approval_id"], process_id=900,
                                      window_class="AppWnd", **{**kw, "text": "again"})
    assert r["verified"] is False and "differs" in r["note"]


def test_automation_control_unit():
    c = AutomationControl()
    assert c.status()["state"] == "idle"
    token = c.begin("x.y", "doing x", mode="observing")
    assert c.status()["state"] == "active" and c.status()["activities"][0]["mode"] == "observing"
    c.end(token)
    c.end(token)  # idempotent
    with pytest.raises(ValueError):
        c.begin("x", "y", mode="spying")
    calls = []
    c.on_stop(lambda: calls.append(1) or 2)
    c.on_stop(lambda: 1 / 0)  # a failing listener cannot break the stop switch
    t = c.begin("a.b", "x")
    result = c.request_stop()
    assert result["activities_interrupted"] == 1 and result["background_tasks_cancelled"] == 2
    with pytest.raises(AutomationStopped):
        c.begin("a.b", "again")
    with pytest.raises(AutomationStopped):
        c.assert_active()
    c.end(t)
    c.resume()
    c.assert_active()


@pytest.mark.skipif(os.name != "posix", reason="uses bash")
def test_stop_all_api_cancels_running_commands_and_blocks_until_resume(tmp_path):
    from mia.computer.filesystem import FilesystemAdapter
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"),
                                   filesystem=fs, approvals=approvals))
    body = {"command": "sleep 30", "root_id": "root_1"}
    aid = client.post("/api/computer/terminal/preview", json=body).json()["approval_id"]
    client.post(f"/api/computer/approvals/{aid}/approve")
    ex = client.post("/api/computer/terminal/execute", json={**body, "approval_id": aid}).json()
    time.sleep(0.2)
    act = client.get("/api/computer/activity").json()
    assert act["state"] == "active" and act["activities"][0]["kind"] == "terminal.run"

    stop = client.post("/api/computer/stop-all").json()
    assert stop["status"] == "stopped" and stop["background_tasks_cancelled"] == 1
    final = client.get(f"/api/computer/terminal/executions/{ex['execution_id']}?wait_seconds=5").json()
    assert final["status"] == "cancelled"
    assert client.get("/api/computer/activity").json()["state"] == "stopped"

    aid2 = client.post("/api/computer/terminal/preview", json={**body, "command": "echo hi"}).json()["approval_id"]
    client.post(f"/api/computer/approvals/{aid2}/approve")
    blocked = client.post("/api/computer/terminal/execute",
                          json={**body, "command": "echo hi", "approval_id": aid2})
    assert blocked.status_code == 423
    assert client.post("/api/computer/resume").json()["state"] == "idle"
    ok = client.post("/api/computer/terminal/execute",
                     json={**body, "command": "echo hi", "approval_id": aid2, "wait_seconds": 10})
    assert ok.status_code == 200 and ok.json()["stdout"].strip() == "hi"


def test_desktop_api_routes(tmp_path):
    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    desktop = FDesktop()
    adapter = WindowsUIAdapter(approvals, desktop_factory=lambda: desktop, platform_name="win32")
    win = FWin(11, "Notes", [])
    save = FControl("btnSave", "Button", "Save", on_act=lambda c: win.controls.append(
        FControl("lbl", "Text", "Saved!")))
    win.controls.append(save)
    desktop.wins.append(win)
    control = AutomationControl()
    ctl = DesktopController(adapter, approvals, control=control, sleep=lambda s: None,
                            screenshot_dir=tmp_path / "s")
    client = TestClient(create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"),
                                   approvals=approvals, windows_ui=adapter,
                                   desktop_controller=ctl, automation_control=control))
    obs = client.get("/api/desktop/windows/11/observe").json()
    assert obs["controls"][0]["name"] == "Save"
    assert client.get("/api/desktop/windows/999/observe").status_code == 403
    body = {"action": "click", "window_handle": 11, "automation_id": "btnSave",
            "control_type": "Button", "name": "Save"}
    pv = client.post("/api/desktop/actions/preview", json=body)
    assert pv.status_code == 201
    aid = pv.json()["approval_id"]
    ex = {**body, "approval_id": aid, "window_title": "Notes"}
    assert client.post("/api/desktop/actions/execute", json=ex).status_code == 409
    client.post(f"/api/computer/approvals/{aid}/approve")
    done = client.post("/api/desktop/actions/execute", json=ex)
    assert done.status_code == 200 and done.json()["verified"] is True
    assert client.post("/api/desktop/actions/preview", json={**body, "action": "hack"}).status_code == 422
    assert client.post("/api/desktop/actions/preview", json={**body, "extra": 1}).status_code == 422
    client.post("/api/computer/stop-all")
    assert client.post("/api/desktop/actions/preview", json=body).status_code == 423


def test_stop_switch_never_consumes_approvals_in_any_executor(tmp_path):
    import subprocess
    import sys

    from mia.computer.fileops import FileOperations
    from mia.computer.filesystem import FilesystemAdapter
    from mia.computer.processes import ProcessManager

    approvals = ApprovalStore(tmp_path / "a.sqlite3")
    control = AutomationControl()
    fs = FilesystemAdapter([tmp_path / "root"], approvals)
    (tmp_path / "root" / "f.txt").write_text("x")
    ops = FileOperations(fs, control=control)
    pv = ops.preview("delete", "root_1", "f.txt")
    approvals.approve(pv["approval_id"])
    control.request_stop()
    with pytest.raises(AutomationStopped):
        ops.execute("delete", pv["approval_id"], "root_1", "f.txt")
    control.resume()
    assert ops.execute("delete", pv["approval_id"], "root_1", "f.txt")["verified"]

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        pm = ProcessManager(approvals, control=control, sample_interval=0.05)
        pp = pm.preview_terminate(child.pid)
        approvals.approve(pp["approval_id"])
        control.request_stop()
        with pytest.raises(AutomationStopped):
            pm.execute_terminate(pp["approval_id"], child.pid)
        assert child.poll() is None
        control.resume()
        assert pm.execute_terminate(pp["approval_id"], child.pid)["verified"]
    finally:
        child.kill()
        child.wait()
