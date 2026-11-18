from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.computer.windows_ui import WindowsUIAdapter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeControl:
    def __init__(self):
        self.text = None

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def set_edit_text(self, text):
        self.text = text


class FakeWindow:
    handle = 123
    element_info = type("Info", (), {"class_name": "Notepad"})()

    def __init__(self):
        self.title = "Notepad"
        self.control = FakeControl()

    def window_text(self):
        return self.title

    def process_id(self):
        return 456

    def set_focus(self):
        pass

    def child_window(self, *, auto_id, control_type):
        assert auto_id == "editor"
        assert control_type == "Edit"
        return type("Wrapper", (), {"wrapper_object": lambda _: self.control})()


class FakeDesktop:
    def __init__(self, window):
        self.window = window

    def windows(self, visible_only=True):
        return [self.window]


def test_windows_ui_preview_and_execution_api_are_approval_gated(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    window = FakeWindow()
    adapter = WindowsUIAdapter(
        approvals,
        desktop_factory=lambda: FakeDesktop(window),
        platform_name="win32",
    )
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            approvals=approvals,
            windows_ui=adapter,
        )
    )

    listed = client.get("/api/desktop/windows")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["title"] == "Notepad"

    preview = adapter.preview_type_text(
        window_handle=123,
        window_title="Notepad",
        automation_id="editor",
        control_type="Edit",
        text="Approved desktop test",
    )
    payload = preview["payload"]

    executed = client.post(
        f"/api/approvals/{preview['approval_id']}/execute",
        json={"kind": "desktop.type_text", "payload": payload},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "completed"
    assert window.control.text == "Approved desktop test"

    replay = client.post(
        f"/api/approvals/{preview['approval_id']}/execute",
        json={"kind": "desktop.type_text", "payload": payload},
    )
    assert replay.status_code == 409
    assert window.control.text == "Approved desktop test"


def test_approval_action_kind_rejects_unknown_action(tmp_path):
    client = TestClient(
        create_app(ObservatoryStore(tmp_path / "observatory.sqlite3"))
    )
    response = client.post(
        "/api/approvals/not-an-approval/execute",
        json={"kind": "shell.execute", "payload": {"command": "whoami"}},
    )
    assert response.status_code == 422
