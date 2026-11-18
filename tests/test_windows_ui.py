from __future__ import annotations

import pytest

from mia.computer.approvals import ApprovalError, ApprovalStore
from mia.computer.windows_ui import DesktopAccessError, WindowsUIAdapter


class FakeControl:
    def __init__(self):
        self.text = None
        self.element_info = type(
            "ControlInfo",
            (),
            {
                "control_type": "Edit",
                "automation_id": "editor",
                "name": "Document",
                "class_name": "Edit",
                "is_password": False,
            },
        )()

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def set_edit_text(self, text):
        self.text = text


class FakeWindow:
    def __init__(self, handle=123, title="Notepad"):
        self.handle = handle
        self._title = title
        self.pid = 456
        self.control = FakeControl()
        self.focused = False
        self.element_info = type("Info", (), {"class_name": "Notepad"})()

    def window_text(self):
        return self._title

    def process_id(self):
        return self.pid

    def set_focus(self):
        self.focused = True

    def descendants(self):
        return [self.control]

    def child_window(self, *, auto_id, control_type):
        assert auto_id == "editor"
        assert control_type == "Edit"
        return type("Wrapper", (), {"wrapper_object": lambda _: self.control})()


class FakeDesktop:
    def __init__(self, window):
        self.window = window

    def windows(self, visible_only=True):
        assert visible_only is True
        return [self.window]


def build_adapter(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    window = FakeWindow()
    adapter = WindowsUIAdapter(
        approvals,
        desktop_factory=lambda: FakeDesktop(window),
        platform_name="win32",
    )
    return adapter, approvals, window


def test_windows_adapter_lists_only_visible_window_metadata(tmp_path):
    adapter, _, _ = build_adapter(tmp_path)
    assert adapter.list_windows() == [{
        "handle": 123,
        "title": "Notepad",
        "process_id": 456,
        "class_name": "Notepad",
    }]


def test_text_entry_requires_exact_one_time_approval(tmp_path):
    adapter, approvals, window = build_adapter(tmp_path)
    preview = adapter.preview_type_text(
        window_handle=123,
        window_title="Notepad",
        automation_id="editor",
        control_type="Edit",
        text="Mia test content",
    )
    assert preview["status"] == "pending"
    assert window.control.text is None

    with pytest.raises(ApprovalError):
        adapter.execute_type_text(
            approval_id=preview["approval_id"],
            window_handle=123,
            window_title="Notepad",
            automation_id="editor",
            control_type="Edit",
            text="Different content",
            process_id=456,
            window_class="Notepad",
        )

    approvals.approve(preview["approval_id"])
    result = adapter.execute_type_text(
        approval_id=preview["approval_id"],
        window_handle=123,
        window_title="Notepad",
        automation_id="editor",
        control_type="Edit",
        text="Mia test content",
        process_id=456,
        window_class="Notepad",
    )
    assert result["characters_entered"] == len("Mia test content")
    assert window.focused is True
    assert window.control.text == "Mia test content"

    with pytest.raises(ApprovalError):
        adapter.execute_type_text(
            approval_id=preview["approval_id"],
            window_handle=123,
            window_title="Notepad",
            automation_id="editor",
            control_type="Edit",
            text="Mia test content",
            process_id=456,
            window_class="Notepad",
        )


def test_windows_adapter_rejects_window_title_change_and_unsafe_control(tmp_path):
    adapter, _, window = build_adapter(tmp_path)
    window._title = "Different window"
    with pytest.raises(DesktopAccessError):
        adapter.preview_type_text(
            window_handle=123,
            window_title="Notepad",
            automation_id="editor",
            control_type="Edit",
            text="hello",
        )

    with pytest.raises(DesktopAccessError):
        adapter.preview_type_text(
            window_handle=123,
            window_title="Different window",
            automation_id="button",
            control_type="Button",
            text="click",
        )


def test_windows_adapter_refuses_non_windows_platform(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    adapter = WindowsUIAdapter(approvals, platform_name="linux")
    with pytest.raises(RuntimeError, match="only on Windows"):
        adapter.list_windows()


def test_window_inspection_returns_control_metadata_without_control_values(tmp_path):
    adapter, _, _ = build_adapter(tmp_path)
    result = adapter.inspect_window(123)
    assert result["window_title"] == "Notepad"
    assert result["controls"] == [{
        "automation_id": "editor",
        "control_type": "Edit",
        "name": "Document",
        "class_name": "Edit",
    }]
    assert "text" not in result["controls"][0]


def test_window_inspection_rejects_unknown_window_and_bad_limit(tmp_path):
    adapter, _, _ = build_adapter(tmp_path)
    with pytest.raises(DesktopAccessError):
        adapter.inspect_window(999)
    with pytest.raises(ValueError):
        adapter.inspect_window(123, limit=201)


def test_approved_text_cannot_be_redirected_to_reused_window_handle(tmp_path):
    adapter, approvals, window = build_adapter(tmp_path)
    preview = adapter.preview_type_text(
        window_handle=123,
        window_title="Notepad",
        automation_id="editor",
        control_type="Edit",
        text="Do not redirect",
    )
    approvals.approve(preview["approval_id"])
    window.pid = 999

    with pytest.raises(DesktopAccessError, match="process has changed"):
        adapter.execute_type_text(
            approval_id=preview["approval_id"],
            window_handle=123,
            window_title="Notepad",
            automation_id="editor",
            control_type="Edit",
            text="Do not redirect",
            process_id=456,
            window_class="Notepad",
        )
    assert window.control.text is None
