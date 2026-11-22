"""Owner-facing HTTP routes for the notification center (MIA-LIFE-09).

There is deliberately no "create notification" route: only Mia's own code may submit
notifications (``NotificationCenter.submit``), so a web page or another local process cannot
inject messages that appear to come from Mia.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from mia.life.notifications import (
    NotificationCenter,
    NotificationNotFound,
    NotificationSettings,
)

_LISTABLE = {"pending", "read", "held", "snoozed", "dismissed", "expired", "suppressed"}


class NotificationSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    inbox_enabled: bool | None = None
    voice_enabled: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    quiet_hours_enabled: bool | None = None
    quiet_start_minute: int | None = None
    quiet_end_minute: int | None = None
    urgent_bypasses_quiet: bool | None = None
    min_relevance: float | None = None
    max_per_day: int | None = None
    min_gap_minutes: int | None = None
    default_expiry_hours: int | None = None
    default_snooze_minutes: int | None = None
    dismiss_cooldown_hours: int | None = None


class SnoozeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minutes: int | None = None


def register_notification_routes(
    app: FastAPI, center: NotificationCenter, producers: list[Callable[[], Any]] | None = None
) -> None:
    def _produce() -> None:
        for producer in producers or []:
            try:
                producer()
            except Exception:  # a failing source must not break the owner's inbox
                continue

    def _guard(action: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return action(*args, **kwargs)
        except NotificationNotFound as exc:
            raise HTTPException(status_code=404, detail="Notification not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/notifications")
    def list_notifications(state: str = "visible", limit: int = 50) -> dict[str, Any]:
        _produce()
        if state == "visible":
            states: tuple[str, ...] = ("pending", "read")
        elif state in _LISTABLE:
            states = (state,)
        else:
            raise HTTPException(status_code=400, detail="Unknown state filter")
        return {
            "notifications": center.list(states=states, limit=limit),
            "counts": center.counts(),
            "settings": asdict(center.get_settings()),
        }

    @app.post("/api/notifications/voice-digest")
    def voice_digest() -> dict[str, Any]:
        """Items to mention at voice-session start; each is returned at most once."""
        _produce()
        items = center.voice_digest(limit=3)
        return {"items": [
            {"notification_id": i["notification_id"], "title": i["title"], "priority": i["priority"]} for i in items
        ]}

    @app.get("/api/notifications/settings")
    def get_notification_settings() -> dict[str, Any]:
        return {"settings": asdict(center.get_settings())}

    @app.put("/api/notifications/settings")
    def put_notification_settings(item: NotificationSettingsInput) -> dict[str, Any]:
        changes = item.model_dump(exclude_none=True)
        if not changes:
            raise HTTPException(status_code=400, detail="No settings provided")
        updated: NotificationSettings = _guard(center.update_settings, changes)
        return {"settings": asdict(updated)}

    @app.get("/api/notifications/{notification_id}/explain")
    def explain_notification(notification_id: str) -> dict[str, Any]:
        return _guard(center.explain, notification_id)

    @app.post("/api/notifications/{notification_id}/read")
    def read_notification(notification_id: str) -> dict[str, Any]:
        return {"notification": _guard(center.mark_read, notification_id)}

    @app.post("/api/notifications/{notification_id}/dismiss")
    def dismiss_notification(notification_id: str) -> dict[str, Any]:
        return {"notification": _guard(center.dismiss, notification_id)}

    @app.post("/api/notifications/{notification_id}/snooze")
    def snooze_notification(notification_id: str, item: SnoozeInput) -> dict[str, Any]:
        return {"notification": _guard(center.snooze, notification_id, minutes=item.minutes)}
