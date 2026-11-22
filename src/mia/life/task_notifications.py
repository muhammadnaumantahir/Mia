"""Turn due and overdue tasks/reminders (MIA-LIFE-02) into notifications (MIA-LIFE-09).

Pull-based: ``sync_due_tasks`` is called when the owner opens the notification list, so it
works without a background scheduler and catches up after downtime. Private items never put
their title or details into a notification (it could be read aloud or glanced at), and items
that were completed, cancelled or deleted since are withdrawn.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mia.life.notifications import NotificationCenter, _zone

SOURCE = "task"
_RELEVANCE = {1: 0.9, 2: 0.7, 3: 0.4}
_PRIORITY = {1: "normal", 2: "normal", 3: "low"}


def _local_text(moment: datetime, timezone: str) -> str:
    return moment.astimezone(_zone(timezone)).strftime("%a %d %b %H:%M")


def sync_due_tasks(
    personal: Any, center: NotificationCenter, *, now: datetime | None = None,
    horizon_hours: int = 24, limit: int = 200,
) -> dict[str, int]:
    """Offer a notification for each open item due within the horizon or already overdue."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    settings = center.get_settings()
    submitted = withdrawn = 0
    horizon = now + timedelta(hours=horizon_hours)
    items = personal.list(status="open", due_before=horizon.isoformat(), limit=limit)
    for item in items:
        if item.get("kind") not in {"task", "reminder"} or not item.get("due_at"):
            continue
        due = datetime.fromisoformat(item["due_at"])
        overdue = due <= now
        priority = int(item.get("priority") or 2)
        label = "Reminder" if item["kind"] == "reminder" else "Task"
        private = bool(item.get("private"))
        title = f"Private {label.lower()} {'overdue' if overdue else 'due soon'}" if private else (
            f"{label} {'overdue' if overdue else 'due soon'}: {item['title']}")
        when = _local_text(due, settings.timezone)
        reason = (f"This {label.lower()} was due {when} ({settings.timezone}) and is still open."
                  if overdue else f"This {label.lower()} is due {when} ({settings.timezone}).")
        hours_left = max(1, int((due - now).total_seconds() // 3600) + 24) if not overdue else 24
        result = center.submit(
            source_type=SOURCE, source_ref=item["item_id"], title=title,
            body="" if private else (item.get("details") or "")[:500],
            reason=reason, priority="urgent" if (overdue and priority == 1) else _PRIORITY.get(priority, "normal"),
            relevance=_RELEVANCE.get(priority, 0.7), dedupe_key=f"task-due:{item['item_id']}:{item['due_at']}",
            evidence=[{"type": "personal_item", "ref": item["item_id"]}],
            expires_in_hours=min(hours_left, 24 * 14), now=now)
        if result["decision"] == "queued":
            submitted += 1
    for note in center.open_for_source(SOURCE):
        current = personal.get(note["source_ref"])
        still_due = current is not None and current.get("status") == "open" and current.get("due_at") \
            and f"task-due:{note['source_ref']}:{current['due_at']}" == note["dedupe_key"]
        if not still_due and center.withdraw(note["notification_id"], "task changed, completed or removed", now):
            withdrawn += 1
    return {"submitted": submitted, "withdrawn": withdrawn}
