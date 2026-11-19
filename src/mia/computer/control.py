"""Shared automation indicator and emergency stop.

Every computer action registers an *activity* while it observes, interacts or executes, so the
owner can always see what Mia is doing (``GET /api/computer/activity``). ``request_stop`` is the
immediate stop switch: it cancels what can be cancelled and blocks new activity until the owner
calls ``resume``. Nothing here grants permission; it can only restrict.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any


class AutomationStopped(PermissionError):
    """Automation was stopped by the owner and has not been resumed."""


class AutomationControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}
        self._stop_requested = False
        self._stopped_at: str | None = None
        self._listeners: list[Any] = []

    @property
    def stop_requested(self) -> bool:
        return self._stop_requested

    def on_stop(self, callback: Any) -> None:
        """Register a callable that cancels in-flight work when the stop switch is used."""
        self._listeners.append(callback)

    def begin(self, kind: str, description: str, *, mode: str = "executing") -> str:
        if mode not in {"observing", "interacting", "executing"}:
            raise ValueError("mode must be observing, interacting or executing")
        with self._lock:
            if self._stop_requested:
                raise AutomationStopped(
                    "Automation was stopped by the owner; resume it before running more actions")
            token = uuid.uuid4().hex
            self._active[token] = {
                "id": token, "kind": kind, "mode": mode, "description": description[:200],
                "since": datetime.now(UTC).isoformat(),
            }
            return token

    def end(self, token: str) -> None:
        with self._lock:
            self._active.pop(token, None)

    @contextmanager
    def activity(self, kind: str, description: str, *, mode: str = "executing"):
        token = self.begin(kind, description, mode=mode)
        try:
            yield token
        finally:
            self.end(token)

    def assert_active(self) -> None:
        if self._stop_requested:
            raise AutomationStopped("Automation was stopped by the owner")

    def request_stop(self) -> dict[str, Any]:
        with self._lock:
            self._stop_requested = True
            self._stopped_at = datetime.now(UTC).isoformat()
            interrupted = list(self._active.values())
        cancelled = 0
        for callback in self._listeners:
            try:
                cancelled += int(callback() or 0)
            except Exception:
                pass
        return {"stopped_at": self._stopped_at, "activities_interrupted": len(interrupted),
                "background_tasks_cancelled": cancelled}

    def resume(self) -> None:
        with self._lock:
            self._stop_requested = False
            self._stopped_at = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = sorted(self._active.values(), key=lambda a: a["since"])
            state = "stopped" if self._stop_requested else ("active" if active else "idle")
            return {"state": state, "activities": active, "stop_requested": self._stop_requested,
                    "stopped_at": self._stopped_at}


def activity_or_null(control: AutomationControl | None, kind: str, description: str,
                     mode: str = "executing"):
    """Context manager: a visible, stop-aware activity, or a no-op when no control is attached."""
    if control is None:
        from contextlib import nullcontext
        return nullcontext()
    return control.activity(kind, description, mode=mode)
