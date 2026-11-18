"""Windows UI Automation adapter with exact-target, approval-bound text entry."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from typing import Any

from mia.computer.approvals import ApprovalStore


class DesktopAccessError(PermissionError):
    """The requested desktop target is unavailable or outside the adapter policy."""


_ALLOWED_TEXT_CONTROLS = {"Edit", "Document", "RichEdit", "ComboBox"}


class WindowsUIAdapter:
    """Inspect visible windows and prepare one exact text-control replacement.

    No mouse automation, keyboard shortcuts, arbitrary keystrokes, menus,
    confirmation buttons or process launch are included in this first adapter.
    """

    MAX_TEXT_CHARS = 20_000
    MAX_WINDOWS = 100

    def __init__(
        self,
        approvals: ApprovalStore,
        *,
        desktop_factory: Callable[[], Any] | None = None,
        platform_name: str | None = None,
    ) -> None:
        self.approvals = approvals
        self.desktop_factory = desktop_factory
        self.platform_name = platform_name

    def _desktop(self):
        platform = self.platform_name or sys.platform
        if platform != "win32":
            raise RuntimeError("Windows desktop automation is available only on Windows")
        if self.desktop_factory:
            return self.desktop_factory()
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise RuntimeError(
                'Install Mia with the "computer" extra to enable Windows UI Automation.'
            ) from exc
        return Desktop(backend="uia")

    def list_windows(self, limit: int = 50) -> list[dict[str, int | str]]:
        safe_limit = max(1, min(int(limit), self.MAX_WINDOWS))
        windows = []
        for window in self._desktop().windows(visible_only=True):
            try:
                title = str(window.window_text() or "").strip()
                if not title:
                    continue
                windows.append({
                    "handle": int(window.handle),
                    "title": title[:240],
                    "process_id": int(window.process_id()),
                    "class_name": str(window.element_info.class_name or "")[:120],
                })
            except Exception:
                continue
            if len(windows) >= safe_limit:
                break
        return windows

    def inspect_window(self, window_handle: int, *, limit: int = 200) -> dict[str, Any]:
        if isinstance(window_handle, bool) or not isinstance(window_handle, int) or window_handle <= 0:
            raise ValueError("window_handle must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        desktop = self._desktop()
        window = next(
            (
                candidate
                for candidate in desktop.windows(visible_only=True)
                if int(candidate.handle) == window_handle
            ),
            None,
        )
        if window is None:
            raise DesktopAccessError("The target window is no longer available")
        controls = []
        for control in window.descendants():
            try:
                info = control.element_info
                if not control.is_visible() or not control.is_enabled():
                    continue
                if bool(getattr(info, "is_password", False)):
                    continue
                control_type = str(getattr(info, "control_type", "") or "")
                automation_id = str(getattr(info, "automation_id", "") or "")
                name = str(getattr(info, "name", "") or "")
                if not automation_id:
                    continue
                if control_type not in _ALLOWED_TEXT_CONTROLS:
                    continue
                controls.append({
                    "automation_id": automation_id[:160],
                    "control_type": control_type,
                    "name": name[:160],
                    "class_name": str(getattr(info, "class_name", "") or "")[:120],
                })
            except Exception:
                continue
            if len(controls) >= limit:
                break
        return {
            "window_handle": window_handle,
            "window_title": str(window.window_text() or "")[:240],
            "process_id": int(window.process_id()),
            "window_class": str(window.element_info.class_name or "")[:120],
            "controls": controls,
            "content_trust": "local_ui_metadata_not_control_text",
        }

    def preview_type_text(
        self,
        *,
        window_handle: int,
        window_title: str,
        automation_id: str,
        control_type: str,
        text: str,
    ) -> dict[str, Any]:
        self._validate_request(window_handle, window_title, automation_id, control_type, text)
        window, control = self._resolve_control(
            window_handle,
            window_title,
            automation_id,
            control_type,
        )
        del control  # Inspection only; no text is entered during preview.
        process_id = int(window.process_id())
        class_name = str(window.element_info.class_name or "")[:120]

        payload = {
            "window_handle": int(window_handle),
            "window_title": window_title,
            "process_id": process_id,
            "window_class": class_name,
            "automation_id": automation_id,
            "control_type": control_type,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        approval = self.approvals.request(
            action_id="desktop.type_text",
            payload=payload,
            summary=(
                f"Replace text in {window_title[:80]} "
                f"({control_type} · {automation_id})"
            ),
        )
        return {
            **approval,
            "kind": "desktop.type_text",
            "payload": payload,
            "display": {
                "window_title": window_title,
                "window_handle": int(window_handle),
                "process_id": process_id,
                "window_class": class_name,
                "control_type": control_type,
                "automation_id": automation_id,
                "text_length": len(text),
                "text_sha256": payload["text_sha256"],
            },
        }

    def execute_type_text(
        self,
        *,
        approval_id: str,
        window_handle: int,
        window_title: str,
        automation_id: str,
        control_type: str,
        text: str,
        process_id: int,
        window_class: str,
    ) -> dict[str, Any]:
        self._validate_request(window_handle, window_title, automation_id, control_type, text)
        if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
            raise ValueError("process_id must be a positive integer")
        if not isinstance(window_class, str) or len(window_class) > 120:
            raise ValueError("window_class must be at most 120 characters")
        payload = {
            "window_handle": int(window_handle),
            "window_title": window_title,
            "process_id": process_id,
            "window_class": window_class,
            "automation_id": automation_id,
            "control_type": control_type,
            "text": text,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        window, control = self._resolve_control(
            window_handle,
            window_title,
            automation_id,
            control_type,
            expected_process_id=process_id,
            expected_window_class=window_class,
        )
        self.approvals.consume(
            approval_id=approval_id,
            action_id="desktop.type_text",
            payload=payload,
        )
        try:
            window.set_focus()
            control.set_edit_text(text)
        except Exception as exc:
            raise DesktopAccessError(
                "The approved text could not be entered into the selected control"
            ) from exc
        return {
            "window_title": window_title,
            "window_handle": int(window_handle),
            "automation_id": automation_id,
            "characters_entered": len(text),
            "text_sha256": payload["text_sha256"],
        }

    def _resolve_control(
        self,
        window_handle: int,
        window_title: str,
        automation_id: str,
        control_type: str,
        *,
        expected_process_id: int | None = None,
        expected_window_class: str | None = None,
    ):
        desktop = self._desktop()
        window = None
        for candidate in desktop.windows(visible_only=True):
            try:
                if int(candidate.handle) == int(window_handle):
                    window = candidate
                    break
            except Exception:
                continue
        if window is None:
            raise DesktopAccessError("The target window is no longer available")
        if str(window.window_text() or "").strip() != window_title:
            raise DesktopAccessError("The target window title has changed")
        if expected_process_id is not None and int(window.process_id()) != expected_process_id:
            raise DesktopAccessError("The target window process has changed")
        current_class = str(window.element_info.class_name or "")[:120]
        if expected_window_class is not None and current_class != expected_window_class:
            raise DesktopAccessError("The target window class has changed")
        try:
            control = window.child_window(
                auto_id=automation_id,
                control_type=control_type,
            ).wrapper_object()
            if not control.is_visible() or not control.is_enabled():
                raise DesktopAccessError("The target text control is not visible and enabled")
            is_password = getattr(control, "is_password", None)
            if callable(is_password) and is_password():
                raise DesktopAccessError("Password controls are not available for text entry")
            if bool(getattr(getattr(control, "element_info", None), "is_password", False)):
                raise DesktopAccessError("Password controls are not available for text entry")
            if not callable(getattr(control, "set_edit_text", None)):
                raise DesktopAccessError("The target control does not support safe text replacement")
            return window, control
        except DesktopAccessError:
            raise
        except Exception as exc:
            raise DesktopAccessError("The exact target text control was not found") from exc

    def _validate_request(
        self,
        window_handle: int,
        window_title: str,
        automation_id: str,
        control_type: str,
        text: str,
    ) -> None:
        if isinstance(window_handle, bool) or not isinstance(window_handle, int) or window_handle <= 0:
            raise ValueError("window_handle must be a positive integer")
        if not isinstance(window_title, str) or not window_title.strip() or len(window_title) > 240:
            raise ValueError("window_title must be 1-240 characters")
        if not isinstance(automation_id, str) or not automation_id.strip() or len(automation_id) > 160:
            raise ValueError("automation_id must be 1-160 characters")
        if control_type not in _ALLOWED_TEXT_CONTROLS:
            raise DesktopAccessError("Only supported text-entry control types are allowed")
        if not isinstance(text, str) or not text or len(text) > self.MAX_TEXT_CHARS:
            raise ValueError("text must be 1-20,000 characters")
