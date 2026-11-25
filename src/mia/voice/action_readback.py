"""MIA-COMP-10: spoken and on-screen read-back of a pending computer action.

The sentence is generated **from the structured action fields** (kind, target, size ...), never
from model prose, so what Mia says and what the card shows cannot drift apart, and a model
cannot talk the owner into approving something different from what will run. The irreversible
part comes first. Every card also gets a short code so the owner can tell which card was
meant when several are waiting.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_CTRL = re.compile(r"[\x00-\x1f\x7f]")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no I, O, 0, 1: hard to mishear or misread


def _clean(value: Any, limit: int = 80) -> str:
    text = _CTRL.sub(" ", str(value if value is not None else "")).strip()
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def confirm_code(approval_id: str) -> str:
    """Four characters derived from the approval id; shown on the card and spoken with it."""
    digest = hashlib.sha256(str(approval_id).encode("utf-8")).digest()
    return "".join(_CODE_ALPHABET[b % len(_CODE_ALPHABET)] for b in digest[:4])


def _size(display: dict[str, Any]) -> str:
    size = display.get("size_bytes")
    return f" ({size} bytes)" if isinstance(size, int) and not isinstance(size, bool) else ""


def describe_action(action: dict[str, Any]) -> dict[str, Any]:
    """Return ``{"sentence", "code", "reversible", "known"}`` for one pending action."""
    kind = str(action.get("kind", ""))
    display = action.get("display") if isinstance(action.get("display"), dict) else {}
    code = confirm_code(str(action.get("approval_id", "")))
    if kind == "filesystem.write":
        target = _clean(display.get("target")) or "a file"
        replaces = bool(display.get("current_sha256"))
        sentence = (
            f"Replace the existing file {target}{_size(display)}. The earlier version is backed up and the change can be undone."
            if replaces else
            f"Create the new file {target}{_size(display)}. The change can be undone."
        )
        return {"sentence": sentence, "code": code, "reversible": True, "known": True}
    if kind == "application.launch":
        name = _clean(display.get("application")) or "an application"
        return {"sentence": f"Open the application {name}. Mia cannot undo this.", "code": code,
                "reversible": False, "known": True}
    if kind == "browser.click":
        selector = _clean(display.get("selector")) or "a control"
        page = _clean(display.get("page_url"), 100) or "the current page"
        return {"sentence": f"Click {selector} on {page}. A click on a website may not be reversible.",
                "code": code, "reversible": False, "known": True}
    if kind == "desktop.type_text":
        window = _clean(display.get("window_title")) or "a window"
        control = _clean(display.get("control_type")) or "a text control"
        return {"sentence": f"Replace the text in the {control} control of the window {window}. Mia cannot undo typed text.",
                "code": code, "reversible": False, "known": True}
    summary = _clean(action.get("summary"), 120) or "an action"
    return {"sentence": f"Do something Mia cannot describe exactly: {summary}. Check the details on screen very carefully.",
            "code": code, "reversible": False, "known": False}
