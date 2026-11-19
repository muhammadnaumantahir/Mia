"""Observe -> understand -> act -> observe -> verify desktop interaction (Windows UI Automation).

Semantic, accessibility-based interaction is preferred (invoke, toggle, select, scroll on a uniquely
identified control). Keyboard entry uses a whitelist of keys plus literal text. Coordinate clicks exist
only as an explicitly approved last resort. Every action:

* is bound by an approval to the exact window (handle, title, process, class) and control identity and
  state, so a different target cannot be driven;
* is refused for security/administration windows (UAC, credential and logon UI, Windows Security,
  registry editor, management consoles, Task Manager);
* registers a visible activity and respects the owner's emergency stop;
* is verified by re-observing the UI. If nothing observable changed, the result says so.

The accessibility tree is read through pywinauto's UIA backend, injected as a factory so the logic is
testable. The real backend has not been exercised in this repository's CI (no Windows host).
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from mia.memory.safety import find_secret

from .approvals import ApprovalStore
from .control import AutomationControl
from .windows_ui import DesktopAccessError, WindowsUIAdapter

ACTIONS = {"click", "toggle", "select", "keys", "scroll", "click_at", "screenshot"}
CLICKABLE = {"Button", "SplitButton", "Hyperlink", "MenuItem", "TabItem", "ListItem", "TreeItem",
             "CheckBox", "RadioButton", "ToggleButton", "DataItem", "Text", "Image"}
TOGGLEABLE = {"CheckBox", "ToggleButton"}
SELECTABLE = {"ListItem", "TabItem", "RadioButton", "TreeItem", "ComboBox", "DataItem"}
KEY_TARGETS = {"Edit", "Document", "RichEdit", "ComboBox", "List", "Tree", "Table", "Pane", "Window"}
EDIT_LIKE = {"Edit", "Document", "RichEdit"}
PROTECTED_PROCESSES = frozenset({
    "consent.exe", "credentialuibroker.exe", "logonui.exe", "sechealthui.exe", "securityhealthsystray.exe",
    "windowsdefender.exe", "regedit.exe", "mmc.exe", "taskmgr.exe", "lsass.exe", "winlogon.exe",
    "credwiz.exe", "secedit.exe", "gpedit.msc", "msmpeng.exe",
})
KEY_MAP = {
    "ENTER": "{ENTER}", "TAB": "{TAB}", "ESC": "{ESC}", "UP": "{UP}", "DOWN": "{DOWN}",
    "LEFT": "{LEFT}", "RIGHT": "{RIGHT}", "HOME": "{HOME}", "END": "{END}", "PAGEUP": "{PGUP}",
    "PAGEDOWN": "{PGDN}", "DELETE": "{DELETE}", "BACKSPACE": "{BACKSPACE}",
    **{f"F{i}": f"{{F{i}}}" for i in range(1, 13)},
    "CTRL+A": "^a", "CTRL+C": "^c", "CTRL+Z": "^z", "CTRL+Y": "^y", "CTRL+F": "^f", "CTRL+S": "^s",
}
_CONSEQUENTIAL = re.compile(
    r"\b(delete|remove|erase|format|send|post|publish|pay|buy|purchase|order|install|uninstall|"
    r"reset|restore|overwrite|replace|transfer|confirm|submit|sign|share|empty|clear|discard|"
    r"don'?t save|exit|quit|close)\b", re.I)


def make_window_guard(process_name_lookup: Any = None):
    """Return a guard that refuses security/admin windows (fails open only if the name is unknown)."""
    def lookup(pid: int) -> str | None:
        if process_name_lookup is not None:
            return process_name_lookup(pid)
        try:
            import psutil
            return psutil.Process(pid).name()
        except Exception:
            return None

    def guard(window: Any) -> None:
        try:
            name = lookup(int(window.process_id()))
        except Exception:
            name = None
        if name and name.casefold() in PROTECTED_PROCESSES:
            raise DesktopAccessError(
                f"{name} is a security or administration window; Mia does not drive it")
    return guard


def keys_to_send(tokens: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Validate key tokens and convert them to pywinauto send_keys syntax."""
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= 50:
        raise ValueError("keys must be a list of 1-50 tokens")
    out: list[str] = []
    clean: list[dict[str, str]] = []
    typed = 0
    for token in tokens:
        if not isinstance(token, dict) or len(token) != 1:
            raise ValueError("each key token must be {'text': ...} or {'key': ...}")
        if "text" in token:
            text = token["text"]
            if not isinstance(text, str) or not text or len(text) > 500:
                raise ValueError("text tokens must be 1-500 characters")
            typed += len(text)
            if typed > 1000:
                raise ValueError("at most 1000 typed characters per action")
            if find_secret(text):
                raise DesktopAccessError("Refusing to type text that looks like a secret")
            out.append("".join(
                "{" + c + "}" if c in "{}()+^%~" else "{SPACE}" if c == " " else c for c in text))
            clean.append({"text": text})
        elif "key" in token:
            key = str(token["key"]).upper()
            if key not in KEY_MAP:
                raise DesktopAccessError(f"Key '{token['key']}' is not allowed")
            out.append(KEY_MAP[key])
            clean.append({"key": key})
        else:
            raise ValueError("each key token must be {'text': ...} or {'key': ...}")
    return "".join(out), clean


class DesktopController:
    MAX_CONTROLS = 400

    def __init__(
        self,
        adapter: WindowsUIAdapter,
        approvals: ApprovalStore,
        *,
        control: AutomationControl | None = None,
        screenshot_dir: str | Path | None = None,
        keyboard: Any = None,
        mouse: Any = None,
        sleep: Any = time.sleep,
        settle_seconds: float = 0.4,
    ) -> None:
        self.adapter = adapter
        self.approvals = approvals
        self.control = control
        self.screenshot_dir = Path(screenshot_dir) if screenshot_dir else None
        self._keyboard = keyboard
        self._mouse = mouse
        self._sleep = sleep
        self.settle_seconds = settle_seconds

    # ---- helpers ------------------------------------------------------------------------
    def _activity(self, kind: str, description: str, mode: str):
        if self.control is None:
            from contextlib import nullcontext
            return nullcontext()
        return self.control.activity(kind, description, mode=mode)

    @staticmethod
    def _info(control: Any) -> dict[str, Any] | None:
        try:
            info = control.element_info
            if bool(getattr(info, "is_password", False)):
                return None
            is_password = getattr(control, "is_password", None)
            if callable(is_password) and is_password():
                return None
            if not control.is_visible():
                return None
            data = {
                "automation_id": str(getattr(info, "automation_id", "") or "")[:160],
                "control_type": str(getattr(info, "control_type", "") or ""),
                "name": str(getattr(info, "name", "") or "")[:160],
                "enabled": bool(control.is_enabled()),
            }
        except Exception:
            return None
        for key, fn in (("toggle_state", "get_toggle_state"), ("selected", "is_selected")):
            reader = getattr(control, fn, None)
            if callable(reader):
                try:
                    data[key] = reader()
                except Exception:
                    pass
        return data

    def _scan(self, window: Any, limit: int | None = None) -> list[tuple[Any, dict[str, Any]]]:
        found = []
        for control in window.descendants():
            info = self._info(control)
            if info is None or not (info["automation_id"] or info["name"]):
                continue
            found.append((control, info))
            if len(found) >= (limit or self.MAX_CONTROLS):
                break
        return found

    @staticmethod
    def _value_hash(control: Any, info: dict[str, Any]) -> str | None:
        if info["control_type"] not in EDIT_LIKE:
            return None
        reader = getattr(control, "get_value", None)
        if not callable(reader):
            return None
        try:
            return hashlib.sha256(str(reader()).encode()).hexdigest()
        except Exception:
            return None

    def _state(self, window: Any) -> dict[str, Any]:
        entries = []
        for control, info in self._scan(window):
            entries.append((info["automation_id"], info["control_type"], info["name"],
                            info["enabled"], str(info.get("toggle_state")),
                            str(info.get("selected")), self._value_hash(control, info)))
        entries.sort(key=lambda e: tuple(str(x) for x in e))
        digest = hashlib.sha256(repr(entries).encode()).hexdigest()
        return {"title": str(window.window_text() or "").strip(), "digest": digest,
                "count": len(entries), "entries": entries}

    def _window_facts(self, window: Any) -> dict[str, Any]:
        return {
            "window_handle": int(window.handle),
            "window_title": str(window.window_text() or "").strip()[:240],
            "process_id": int(window.process_id()),
            "window_class": str(window.element_info.class_name or "")[:120],
        }

    def _locate(self, window: Any, automation_id: str, control_type: str, name: str):
        if not (automation_id or name):
            raise ValueError("automation_id or name is required to identify a control")
        matches = [(c, i) for c, i in self._scan(window)
                   if i["automation_id"] == automation_id and i["control_type"] == control_type
                   and (not name or i["name"] == name)]
        if not matches:
            raise DesktopAccessError("The exact target control was not found")
        if len(matches) > 1:
            raise DesktopAccessError(
                "The target control cannot be identified uniquely (several controls match)")
        return matches[0]

    # ---- observe ------------------------------------------------------------------------
    def observe(self, window_handle: int, *, limit: int = 200) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 400:
            raise ValueError("limit must be between 1 and 400")
        window = self.adapter._find_window(window_handle)
        with self._activity("desktop.observe", f"Reading controls of '{window.window_text()}'",
                            "observing"):
            controls = []
            for _, info in self._scan(window, limit):
                actions = ["click"] if info["control_type"] in CLICKABLE else []
                if info["control_type"] in TOGGLEABLE:
                    actions.append("toggle")
                if info["control_type"] in SELECTABLE:
                    actions.append("select")
                if info["control_type"] in KEY_TARGETS:
                    actions += ["keys", "scroll"]
                controls.append({**info, "actions": actions,
                                 "consequential": bool(_CONSEQUENTIAL.search(info["name"]))})
            return {**self._window_facts(window), "controls": controls,
                    "content_trust": "local_ui_labels_values_not_returned"}

    # ---- plan/preview/execute -------------------------------------------------------------
    def _plan(self, action: str, window_handle: int, window_title: str | None,
              automation_id: str, control_type: str, name: str, params: dict[str, Any]):
        if action not in ACTIONS:
            raise ValueError("Unsupported desktop action")
        window = self.adapter._find_window(window_handle, window_title)
        payload: dict[str, Any] = {"action": action, **self._window_facts(window)}
        control = info = None
        if action not in {"click_at", "screenshot"}:
            control, info = self._locate(window, automation_id, control_type, name)
            if not info["enabled"]:
                raise DesktopAccessError("The target control is disabled")
            kind = info["control_type"]
            allowed = {"click": CLICKABLE, "toggle": TOGGLEABLE, "select": SELECTABLE,
                       "keys": KEY_TARGETS, "scroll": KEY_TARGETS | SELECTABLE}[action]
            if kind not in allowed:
                raise DesktopAccessError(f"A {kind} control does not support '{action}'")
            payload["control"] = {k: info[k] for k in ("automation_id", "control_type", "name")}
            payload["control_state"] = {
                "enabled": info["enabled"], "toggle_state": str(info.get("toggle_state")),
                "selected": str(info.get("selected")),
                "value_sha": self._value_hash(control, info)}
        if action == "keys":
            send, clean = keys_to_send(params.get("keys"))
            payload["keys"] = clean
        elif action == "select" and control_type == "ComboBox":
            value = params.get("value")
            if not isinstance(value, str) or not value or len(value) > 200:
                raise ValueError("value is required to choose from a combo box")
            payload["value"] = value
        elif action == "scroll":
            direction, pages = params.get("direction", "down"), params.get("pages", 1)
            if direction not in {"up", "down"} or isinstance(pages, bool) or not (
                    isinstance(pages, int) and 1 <= pages <= 20):
                raise ValueError("scroll needs direction up/down and pages 1-20")
            payload.update(direction=direction, pages=pages)
        elif action == "click_at":
            x, y = params.get("x"), params.get("y")
            rect = window.rectangle()
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)) or not (
                    0 <= x < width and 0 <= y < height):
                raise ValueError("x and y must be integer positions inside the window")
            payload.update(x=x, y=y, window_size=[width, height])
        elif action == "screenshot":
            if self.screenshot_dir is None:
                raise DesktopAccessError("Screenshots are not configured")
        return window, control, info, payload

    def preview(self, action: str, window_handle: int, *, window_title: str | None = None,
                automation_id: str = "", control_type: str = "", name: str = "",
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._activity("desktop.preview", "Preparing a desktop action for approval",
                            "observing"):
            _, _, info, payload = self._plan(
                action, window_handle, window_title, automation_id, control_type, name,
                params or {})
        target = (info["name"] or info["automation_id"]) if info else "window"
        flag = "CONSEQUENTIAL " if (info and _CONSEQUENTIAL.search(info["name"])) or \
            action in {"click_at", "screenshot"} else ""
        note = {"click_at": " by screen coordinates (fallback, no semantic target)",
                "screenshot": " (captures this window's contents)"}.get(action, "")
        summary = f"{flag}{action} '{target}' in '{payload['window_title'][:60]}'{note}"
        approval = self.approvals.request(
            action_id=f"desktop.{action}", payload=payload, summary=summary[:300])
        return {**approval, "plan": payload}

    def execute(self, approval_id: str, action: str, window_handle: int, window_title: str, *,
                automation_id: str = "", control_type: str = "", name: str = "",
                params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        if self.control is not None:
            self.control.assert_active()  # a stopped system must not burn one-time approvals
        window, control, info, payload = self._plan(
            action, window_handle, window_title, automation_id, control_type, name, params)
        self.approvals.consume(approval_id=approval_id, action_id=f"desktop.{action}",
                               payload=payload)
        target = (info["name"] or info["automation_id"]) if info else "window"
        with self._activity(f"desktop.{action}",
                            f"{action} '{target}' in '{payload['window_title'][:60]}'", "interacting"):
            before = self._state(window)
            windows_before = {w["handle"] for w in self.adapter.list_windows(limit=100)}
            if action == "screenshot":
                return self._screenshot(window, payload)
            try:
                self._perform(action, window, control, info, payload)
            except DesktopAccessError:
                raise
            except Exception as exc:
                raise DesktopAccessError(
                    f"The {action} could not be performed: {type(exc).__name__}") from exc
            self._sleep(self.settle_seconds)
            return self._verify(action, payload, before, windows_before, info)

    def _perform(self, action: str, window: Any, control: Any, info: Any,
                 payload: dict[str, Any]) -> None:
        window.set_focus()
        if action == "click":
            invoke = getattr(control, "invoke", None)
            if callable(invoke) and info["control_type"] in {"Button", "SplitButton", "MenuItem",
                                                              "Hyperlink"}:
                invoke()
            else:
                control.click_input()
        elif action == "toggle":
            control.toggle()
        elif action == "select":
            if info["control_type"] == "ComboBox":
                control.select(payload["value"])
            else:
                control.select()
        elif action == "keys":
            control.set_focus()
            send, _ = keys_to_send(payload["keys"])
            self._send_keys()(send, with_spaces=False, pause=0.01)
        elif action == "scroll":
            control.scroll(payload["direction"], "page", payload["pages"])
        elif action == "click_at":
            rect = window.rectangle()
            self._mouse_module().click(button="left", coords=(rect.left + payload["x"],
                                                              rect.top + payload["y"]))

    def _send_keys(self):
        if self._keyboard is not None:
            return self._keyboard
        from pywinauto.keyboard import send_keys
        return send_keys

    def _mouse_module(self):
        if self._mouse is not None:
            return self._mouse
        from pywinauto import mouse
        return mouse

    def _screenshot(self, window: Any, payload: dict[str, Any]) -> dict[str, Any]:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = f"window-{payload['window_handle']}-{int(time.time())}.png"
        target = self.screenshot_dir / name
        try:
            window.capture_as_image().save(str(target))
        except Exception as exc:
            raise DesktopAccessError("The window could not be captured") from exc
        size = target.stat().st_size if target.exists() else 0
        return {"action": "screenshot", "path": str(target), "bytes": size, "verified": size > 0,
                "note": None if size else "The capture produced an empty file."}

    def _verify(self, action: str, payload: dict[str, Any], before: dict[str, Any],
                windows_before: set[int], info: dict[str, Any] | None) -> dict[str, Any]:
        """Re-observe the UI and decide, from evidence, whether the action had its effect."""
        try:
            window = self.adapter._find_window(payload["window_handle"])
        except DesktopAccessError:
            window = None
        windows_after = {w["handle"] for w in self.adapter.list_windows(limit=100)}
        effects: dict[str, Any] = {
            "window_closed": window is None,
            "new_windows": len(windows_after - windows_before),
        }
        verified, note = False, None
        if window is None:
            verified = True
            note = "The window closed as a result of the action."
        else:
            after = self._state(window)
            effects.update(
                title_changed=after["title"] != before["title"],
                content_changed=after["digest"] != before["digest"],
                controls_before=before["count"], controls_after=after["count"])
            target_now = None
            if info is not None:
                for _, candidate in self._scan(window):
                    if (candidate["automation_id"], candidate["control_type"], candidate["name"]) == \
                            (info["automation_id"], info["control_type"], info["name"]):
                        target_now = candidate
                        break
                effects["target_present"] = target_now is not None
            any_change = effects["title_changed"] or effects["content_changed"] \
                or effects["new_windows"] > 0
            if action == "toggle":
                verified = bool(target_now) and str(target_now.get("toggle_state")) != \
                    payload["control_state"]["toggle_state"]
                note = None if verified else "The toggle state did not change."
            elif action == "select":
                verified = bool(target_now) and (
                    target_now.get("selected") is True or (info["control_type"] == "ComboBox"
                                                          and any_change))
                note = None if verified else "The item does not report as selected."
            else:
                verified = any_change or (info is not None and not effects.get("target_present", True))
                if not verified:
                    note = ("The action was delivered but no change in the window was observed; "
                            "the target may be inactive, blocked by a dialog, or the app may have "
                            "ignored it.")
        return {"action": action, "verified": verified, "effects": effects, "note": note}
