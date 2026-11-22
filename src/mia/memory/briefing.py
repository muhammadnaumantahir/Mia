"""Daily and weekly briefings from owner-approved sources only (MIA-LIFE-05).

Deterministic and model-free: a briefing is assembled from what is stored, never written
by a model, so every line can be traced to a record.

Sources used: open tasks and reminders (``personal_items``), open project milestones and
the goals of active projects (``projects``). Nothing else is consulted. In particular there
is no calendar source, so no calendar event is ever listed; the briefing says so under
``missing_sources`` instead of guessing.

- Every item cites its source record (type and id); the briefing reports its timezone
  (the owner's ``owner_timezone`` setting), when it was generated and how fresh each source is.
- Items marked ``private`` are left out (only counted) unless ``include_private`` is set.
- Quiet hours are a delivery signal: content is still built, but ``deliver_now`` is false
  while the owner's local time is inside the window. Nothing is sent from here.
- Empty sources are reported as empty; nothing is invented to fill a briefing.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from mia.memory.store import MemoryStore

PERIODS = {"daily": 1, "weekly": 7}
MAX_PER_SECTION = 50


def _tz(store: MemoryStore) -> tuple[str, ZoneInfo]:
    name = store.settings.get("owner_timezone")
    try:
        return name, ZoneInfo(name)
    except Exception:  # corrupt value: fall back to UTC and say so in the output
        return "UTC", ZoneInfo("UTC")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _in_quiet_hours(local: datetime, start: int | None, end: int | None) -> bool:
    if start is None or end is None or start == end:
        return False
    minute = local.hour * 60 + local.minute
    return start <= minute < end if start < end else minute >= start or minute < end


def _clean_minute(value: int | None, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1439:
        raise ValueError(f"{field} must be a whole number of minutes from 0 to 1439")
    return value


def _short(item_id: str) -> str:
    return item_id[:8]


def generate_briefing(
    store: MemoryStore,
    *,
    period: str = "daily",
    now: datetime | None = None,
    project: str | None = None,
    include_private: bool = False,
    horizon_days: int | None = None,
    stale_goal_days: int = 14,
    max_items: int = 10,
    quiet_start_minute: int | None = None,
    quiet_end_minute: int | None = None,
) -> dict[str, Any]:
    if period not in PERIODS:
        raise ValueError("period must be daily or weekly")
    days = PERIODS[period] if horizon_days is None else horizon_days
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 31:
        raise ValueError("horizon_days must be a whole number from 1 to 31")
    if isinstance(stale_goal_days, bool) or not isinstance(stale_goal_days, int) or not (
        1 <= stale_goal_days <= 365
    ):
        raise ValueError("stale_goal_days must be a whole number from 1 to 365")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not (
        1 <= max_items <= MAX_PER_SECTION
    ):
        raise ValueError(f"max_items must be a whole number from 1 to {MAX_PER_SECTION}")
    quiet_start = _clean_minute(quiet_start_minute, "quiet_start_minute")
    quiet_end = _clean_minute(quiet_end_minute, "quiet_end_minute")
    if (quiet_start is None) != (quiet_end is None):
        raise ValueError("give both quiet_start_minute and quiet_end_minute, or neither")

    tz_name, tz = _tz(store)
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    local_now = now_utc.astimezone(tz)
    end_local = datetime.combine(local_now.date() + timedelta(days=days), time.min).replace(
        tzinfo=tz
    )
    window_end = end_local.astimezone(UTC)

    from mia.memory.store import project_key

    wanted = project_key(project) if project else None
    if project and wanted is None:
        raise ValueError("project name is not valid")

    def in_project(value: str | None) -> bool:
        return wanted is None or project_key(value) == wanted

    def local_text(iso: str) -> str:
        return _parse(iso).astimezone(tz).strftime("%Y-%m-%d %H:%M")

    items = [
        i for i in store.personal.list(status="open", limit=500) if in_project(i["project"])
    ]
    hidden_private = sum(1 for i in items if i["private"] and not include_private)
    if not include_private:
        items = [i for i in items if not i["private"]]
    with_due = sorted(
        (i for i in items if i["due_at"]),
        key=lambda i: (_parse(i["due_at"]), i["priority"], i["item_id"]),
    )

    def personal_entry(i: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": i["kind"], "title": i["title"], "priority": i["priority"],
            "due_at": i["due_at"], "due_local": local_text(i["due_at"]),
            "project": i["project"], "private": i["private"],
            "source": {"type": "personal_item", "id": i["item_id"]},
        }

    overdue = [personal_entry(i) for i in with_due if _parse(i["due_at"]) < now_utc]
    upcoming = [
        personal_entry(i) for i in with_due if now_utc <= _parse(i["due_at"]) < window_end
    ]

    milestone_rows: list[dict[str, Any]] = []
    projects = store.projects.list()
    for proj in projects:
        if not in_project(proj["name"]) or proj["status"] != "active":
            continue
        for m in store.projects.milestones(proj["name"]):
            if m["status"] == "open" and m["due_at"]:
                milestone_rows.append({**m, "project": proj["name"]})
    milestone_rows.sort(key=lambda m: (_parse(m["due_at"]), m["milestone_id"]))

    def milestone_entry(m: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "milestone", "title": m["title"], "priority": None,
            "due_at": m["due_at"], "due_local": local_text(m["due_at"]),
            "project": m["project"], "private": False,
            "source": {"type": "project_milestone", "id": m["milestone_id"]},
        }

    milestones_late = [milestone_entry(m) for m in milestone_rows if _parse(m["due_at"]) < now_utc]
    milestones_soon = [
        milestone_entry(m) for m in milestone_rows if now_utc <= _parse(m["due_at"]) < window_end
    ]

    # Goal follow-up: active projects with a goal and no recent activity of any kind.
    follow_ups: list[dict[str, Any]] = []
    cutoff = now_utc - timedelta(days=stale_goal_days)
    for proj in projects:
        if not in_project(proj["name"]) or proj["status"] != "active" or not proj["goal"]:
            continue
        stamps = [proj["updated_at"]]
        stamps += [
            i["updated_at"] for i in store.personal.list(project=proj["name"], limit=500)
            if include_private or not i["private"]
        ]
        stamps += [m["updated_at"] for m in store.projects.milestones(proj["name"])]
        newest = max(_parse(s) for s in stamps)
        if newest < cutoff:
            idle = (now_utc - newest).days
            follow_ups.append({
                "project": proj["name"], "goal": proj["goal"],
                "reason": f"no task, milestone or goal change recorded for {idle} days",
                "last_activity": newest.isoformat(),
                "source": {"type": "project", "id": proj["project_key"]},
            })

    sections = [
        {"key": "overdue", "title": "Overdue", "items": overdue + milestones_late},
        {"key": "upcoming", "title": "Coming up", "items": upcoming + milestones_soon},
    ]
    omitted = 0
    for section in sections:
        section["items"].sort(key=lambda e: (e["due_at"], e["priority"] or 9, e["source"]["id"]))
        omitted += max(0, len(section["items"]) - max_items)
        section["items"] = section["items"][:max_items]

    newest_update = lambda rows: max((r["updated_at"] for r in rows), default=None)  # noqa: E731
    freshness = {
        "personal_items": {"open_count": len(items), "newest_update": newest_update(items)},
        "milestones": {"open_with_due_count": len(milestone_rows)},
        "projects": {"active_count": sum(1 for p in projects if p["status"] == "active")},
    }
    missing = [{
        "source": "calendar",
        "reason": "No calendar source is connected, so no calendar events are included.",
    }]
    if not items and not milestone_rows:
        missing.append({
            "source": "tasks_and_milestones",
            "reason": "No open tasks or dated milestones were found in the sources used.",
        })
    undated = sum(1 for i in items if not i["due_at"])

    quiet = _in_quiet_hours(local_now, quiet_start, quiet_end)
    result = {
        "period": period,
        "generated_at": now_utc.isoformat(),
        "timezone": tz_name,
        "local_time": local_now.strftime("%Y-%m-%d %H:%M"),
        "window": {"start": now_utc.isoformat(), "end": window_end.isoformat(), "days": days},
        "project": project,
        "include_private": include_private,
        "hidden_private_count": hidden_private,
        "undated_open_items": undated,
        "omitted_by_limit": omitted,
        "sections": sections,
        "follow_ups": follow_ups,
        "freshness": freshness,
        "missing_sources": missing,
        "quiet_hours_active": quiet,
        "deliver_now": not quiet,
    }
    result["text"] = render_text(result)
    return result


def render_text(b: dict[str, Any]) -> str:
    """Plain-text form. Every line carries its source type and short id."""
    lines = [
        f"{b['period'].capitalize()} briefing - {b['local_time']} ({b['timezone']})",
        f"Sources: tasks, reminders, milestones, project goals; generated {b['generated_at']}.",
    ]
    for section in b["sections"]:
        if not section["items"]:
            continue
        lines.append("")
        lines.append(f"{section['title']}:")
        for e in section["items"]:
            tag = {"personal_item": e["kind"], "project_milestone": "milestone"}[e["source"]["type"]]
            prio = {1: " [high]", 3: " [low]"}.get(e["priority"], "")
            proj = f" ({e['project']})" if e["project"] else ""
            lines.append(f"- {e['title']}{prio}{proj} - due {e['due_local']} [{tag} {_short(e['source']['id'])}]")
    if b["follow_ups"]:
        lines += ["", "Goal follow-up:"]
        for f in b["follow_ups"]:
            lines.append(f"- {f['project']}: {f['goal']} - {f['reason']} [project {f['source']['id']}]")
    if not any(s["items"] for s in b["sections"]) and not b["follow_ups"]:
        lines += ["", "Nothing is overdue or due in this window in the sources used."]
    extras = []
    if b["undated_open_items"]:
        extras.append(f"{b['undated_open_items']} open item(s) have no due date")
    if b["hidden_private_count"]:
        extras.append(f"{b['hidden_private_count']} private item(s) hidden")
    if b["omitted_by_limit"]:
        extras.append(f"{b['omitted_by_limit']} more not shown (limit)")
    if extras:
        lines += ["", "Note: " + "; ".join(extras) + "."]
    lines += ["", "Missing sources:"]
    lines += [f"- {m['source']}: {m['reason']}" for m in b["missing_sources"]]
    if b["quiet_hours_active"]:
        lines += ["", "Quiet hours are active: do not deliver this now."]
    return "\n".join(lines)
