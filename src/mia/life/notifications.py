"""MIA-LIFE-09: notification center with an owner-controlled delivery policy.

Pull-based and local: Mia never pushes anything off the machine. Sources call
``NotificationCenter.submit`` with a *reason* and a *source* (both mandatory, so the owner can
always see why a notification exists). The center then applies, in order: validation, the
owner's channel choice, relevance floor, de-duplication, quiet hours (IANA timezone, so DST is
handled), a per-day cap and a minimum gap, expiry and snooze. Nothing here talks to the network,
and every state change is evaluated lazily on read, so a machine that was offline or asleep
catches up correctly the next time it is asked.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CHANNELS = ("inbox", "voice")
PRIORITIES = ("low", "normal", "urgent")
_PRIORITY_RANK = {"urgent": 0, "normal": 1, "low": 2}
OPEN_STATES = frozenset({"held", "pending", "read", "snoozed"})
VISIBLE_STATES = ("pending", "read")
MAX_SNOOZE_MINUTES = 60 * 24 * 30
KEEP_TERMINAL_ROWS = 1000
_SOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,39}$")
_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotificationNotFound(KeyError):
    pass


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    enabled: bool = True
    inbox_enabled: bool = True
    voice_enabled: bool = False  # spoken only at the start of a voice session, opt-in
    timezone: str = "UTC"
    follow_owner_timezone: bool = True  # use the main `owner_timezone` setting unless the owner picks one here
    quiet_hours_enabled: bool = False
    quiet_start_minute: int = 22 * 60  # local minutes after midnight
    quiet_end_minute: int = 7 * 60
    urgent_bypasses_quiet: bool = True
    min_relevance: float = 0.3
    max_per_day: int = 10
    min_gap_minutes: int = 0
    default_expiry_hours: int = 72
    default_snooze_minutes: int = 60
    dismiss_cooldown_hours: int = 168


_INT_RANGES = {
    "quiet_start_minute": (0, 1439),
    "quiet_end_minute": (0, 1439),
    "max_per_day": (0, 200),
    "min_gap_minutes": (0, 1440),
    "default_expiry_hours": (1, 24 * 365),
    "default_snooze_minutes": (1, MAX_SNOOZE_MINUTES),
    "dismiss_cooldown_hours": (0, 24 * 365),
}
_BOOLS = {"follow_owner_timezone", "enabled", "inbox_enabled", "voice_enabled", "quiet_hours_enabled", "urgent_bypasses_quiet"}


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not _TZ_RE.match(name):
        raise ValueError("timezone must be an IANA name such as Asia/Karachi")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError("unknown timezone; use an IANA name such as Asia/Karachi") from exc


def validate_settings(values: dict[str, Any]) -> dict[str, Any]:
    known = {f.name for f in fields(NotificationSettings)}
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"unknown setting: {key}")
        if key in _BOOLS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
            cleaned[key] = value
        elif key == "timezone":
            _zone(value)
            cleaned[key] = value
        elif key == "min_relevance":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("min_relevance must be a number from 0 to 1")
            cleaned[key] = float(value)
        else:
            low, high = _INT_RANGES[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} must be a whole number from {low} to {high}")
            cleaned[key] = value
    return cleaned


def _clean(text: Any, limit: int, name: str, *, required: bool = True) -> str:
    if not isinstance(text, str):
        raise ValueError(f"{name} must be text")
    value = _CTRL_RE.sub("", text).strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    return value[:limit]


class NotificationCenter:
    def __init__(self, database_path: str | Path, owner_timezone: Callable[[], str | None] | None = None) -> None:
        self._owner_timezone = owner_timezone
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notifications (
                    notification_id TEXT PRIMARY KEY,
                    source_type TEXT NOT NULL, source_ref TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL, reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    priority TEXT NOT NULL, relevance REAL NOT NULL, dedupe_key TEXT NOT NULL,
                    state TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                    deliver_after TEXT NOT NULL, expires_at TEXT NOT NULL,
                    released_at TEXT, read_at TEXT, dismissed_at TEXT, snoozed_until TEXT,
                    voice_announced_at TEXT, channels_json TEXT NOT NULL DEFAULT '[]',
                    terminal_note TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_state ON notifications(state);
                CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key);
                CREATE TABLE IF NOT EXISTS notification_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL, detail TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notification_events ON notification_events(notification_id);
                """
            )

    # ---- settings -------------------------------------------------------------------------
    def get_settings(self) -> NotificationSettings:
        with self._db() as db:
            stored = {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM notification_settings")}
        known = {f.name for f in fields(NotificationSettings)}
        try:
            settings = NotificationSettings(**validate_settings({k: v for k, v in stored.items() if k in known}))
        except ValueError:
            settings = NotificationSettings()  # corrupt stored values fall back to safe defaults
        if settings.follow_owner_timezone and self._owner_timezone is not None:
            try:
                name = self._owner_timezone()
                if name:
                    _zone(name)
                    settings = replace(settings, timezone=name)
            except Exception:  # an unreadable or invalid main setting must never break delivery
                pass
        return settings

    def update_settings(self, changes: dict[str, Any]) -> NotificationSettings:
        cleaned = validate_settings(changes)
        if "timezone" in cleaned and "follow_owner_timezone" not in cleaned:
            cleaned["follow_owner_timezone"] = False  # choosing a zone here is an explicit override
        merged = {**asdict(self.get_settings()), **cleaned}
        if merged["quiet_hours_enabled"] and merged["quiet_start_minute"] == merged["quiet_end_minute"]:
            raise ValueError("quiet hours need different start and end times")
        with self._db() as db:
            for key, value in cleaned.items():
                db.execute(
                    "INSERT INTO notification_settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        return self.get_settings()

    # ---- time helpers (IANA zone, so DST is honoured) -------------------------------------
    @staticmethod
    def _local_minute(settings: NotificationSettings, moment: datetime) -> int:
        local = moment.astimezone(_zone(settings.timezone))
        return local.hour * 60 + local.minute

    def in_quiet_hours(self, settings: NotificationSettings, moment: datetime) -> bool:
        if not settings.quiet_hours_enabled:
            return False
        minute = self._local_minute(settings, moment)
        start, end = settings.quiet_start_minute, settings.quiet_end_minute
        if start < end:
            return start <= minute < end
        return minute >= start or minute < end

    @staticmethod
    def _local_instant(settings: NotificationSettings, day, minute: int) -> datetime:
        zone = _zone(settings.timezone)
        wall = datetime.combine(day, time(minute // 60, minute % 60), tzinfo=zone)
        return wall.astimezone(UTC)

    def quiet_end_after(self, settings: NotificationSettings, moment: datetime) -> datetime:
        zone = _zone(settings.timezone)
        local_day = moment.astimezone(zone).date()
        for offset in range(0, 3):
            candidate = self._local_instant(settings, local_day + timedelta(days=offset), settings.quiet_end_minute)
            if candidate > moment:
                return candidate
        return moment + timedelta(hours=24)

    def _day_bounds(self, settings: NotificationSettings, moment: datetime) -> tuple[datetime, datetime]:
        day = moment.astimezone(_zone(settings.timezone)).date()
        return self._local_instant(settings, day, 0), self._local_instant(settings, day + timedelta(days=1), 0)

    # ---- row helpers ------------------------------------------------------------------------
    @staticmethod
    def _iso(moment: datetime) -> str:
        return moment.astimezone(UTC).isoformat()

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["evidence"] = json.loads(item.pop("evidence_json"))
        item["channels"] = json.loads(item.pop("channels_json"))
        return item

    def _event(self, db: sqlite3.Connection, nid: str, at: datetime, event: str, detail: str) -> None:
        db.execute("INSERT INTO notification_events(notification_id, at, event, detail) VALUES(?,?,?,?)",
                   (nid, self._iso(at), event, detail[:300]))

    # ---- submit -----------------------------------------------------------------------------
    def submit(
        self, *, source_type: str, source_ref: str, title: str, body: str, reason: str,
        priority: str = "normal", relevance: float = 0.5, dedupe_key: str | None = None,
        evidence: list[dict[str, str]] | None = None, expires_in_hours: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Offer one notification. Returns ``{"decision", "notification_id", "reason"}``.

        Decisions: ``queued`` (held or delivered per policy), ``deduplicated``,
        ``suppressed`` (policy said no; the reason says why).
        """
        now = (now or utc_now()).astimezone(UTC)
        if not isinstance(source_type, str) or not _SOURCE_RE.match(source_type):
            raise ValueError("source_type must be a short lowercase identifier")
        source_ref = _clean(source_ref, 200, "source_ref")
        title = _clean(title, 200, "title")
        body = _clean(body, 2000, "body", required=False)
        reason = _clean(reason, 500, "reason")
        if priority not in PRIORITIES:
            raise ValueError("priority must be low, normal or urgent")
        if isinstance(relevance, bool) or not isinstance(relevance, (int, float)) or not 0 <= relevance <= 1:
            raise ValueError("relevance must be a number from 0 to 1")
        clean_evidence = [
            {"type": _clean(e.get("type", ""), 40, "evidence type"), "ref": _clean(e.get("ref", ""), 200, "evidence ref")}
            for e in (evidence or [])[:10]
        ]
        key = _clean(dedupe_key or f"{source_type}:{source_ref}", 300, "dedupe_key")
        settings = self.get_settings()
        hours = settings.default_expiry_hours if expires_in_hours is None else expires_in_hours
        if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= 24 * 365:
            raise ValueError("expires_in_hours must be a whole number from 1 to 8760")
        expires = now + timedelta(hours=hours)

        self.refresh(now)
        with self._db() as db:
            if not settings.enabled:
                return {"decision": "suppressed", "notification_id": None, "reason": "notifications_off"}
            existing = db.execute(
                "SELECT * FROM notifications WHERE dedupe_key=? AND state IN ('held','pending','read','snoozed') "
                "ORDER BY created_at DESC LIMIT 1", (key,)).fetchone()
            if existing:
                db.execute("UPDATE notifications SET occurrences=occurrences+1, last_seen_at=?, body=?, "
                           "expires_at=MAX(expires_at, ?) WHERE notification_id=?",
                           (self._iso(now), body, self._iso(expires), existing["notification_id"]))
                self._event(db, existing["notification_id"], now, "deduplicated", "same notification seen again")
                return {"decision": "deduplicated", "notification_id": existing["notification_id"], "reason": "already_open"}
            dismissed = db.execute(
                "SELECT * FROM notifications WHERE dedupe_key=? AND state='dismissed' ORDER BY dismissed_at DESC LIMIT 1",
                (key,)).fetchone()
            if dismissed and dismissed["dismissed_at"] and priority != "urgent":
                cooldown = timedelta(hours=settings.dismiss_cooldown_hours)
                if now - datetime.fromisoformat(dismissed["dismissed_at"]) < cooldown:
                    db.execute("UPDATE notifications SET occurrences=occurrences+1, last_seen_at=? WHERE notification_id=?",
                               (self._iso(now), dismissed["notification_id"]))
                    self._event(db, dismissed["notification_id"], now, "suppressed", "recently dismissed by the owner")
                    return {"decision": "suppressed", "notification_id": dismissed["notification_id"],
                            "reason": "recently_dismissed"}
            nid = f"ntf-{uuid.uuid4().hex[:16]}"
            channels = [c for c, on in (("inbox", settings.inbox_enabled), ("voice", settings.voice_enabled)) if on]
            state, note, after = "held", None, now
            if not channels:
                state, note = "suppressed", "no_channel_enabled"
            elif priority != "urgent" and relevance < settings.min_relevance:
                state, note = "suppressed", "below_relevance_floor"
            elif priority != "urgent" or not settings.urgent_bypasses_quiet:
                if self.in_quiet_hours(settings, now):
                    after = self.quiet_end_after(settings, now)
                    if after >= expires:
                        state, note = "suppressed", "would_expire_during_quiet_hours"
            db.execute(
                "INSERT INTO notifications(notification_id, source_type, source_ref, title, body, reason, evidence_json,"
                " priority, relevance, dedupe_key, state, created_at, last_seen_at, deliver_after, expires_at,"
                " channels_json, terminal_note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, source_type, source_ref, title, body, reason, json.dumps(clean_evidence), priority,
                 float(relevance), key, state, self._iso(now), self._iso(now), self._iso(after), self._iso(expires),
                 json.dumps(channels), note))
            self._event(db, nid, now, state, note or f"accepted; earliest delivery {self._iso(after)}")
        if state == "suppressed":
            return {"decision": "suppressed", "notification_id": nid, "reason": note}
        self.refresh(now)
        return {"decision": "queued", "notification_id": nid, "reason": "accepted"}

    # ---- lazy evaluation --------------------------------------------------------------------
    def refresh(self, now: datetime | None = None) -> None:
        """Apply expiry, snooze wake-ups and the release policy as of ``now``."""
        now = (now or utc_now()).astimezone(UTC)
        settings = self.get_settings()
        with self._db() as db:
            for row in db.execute(
                "SELECT notification_id FROM notifications WHERE state IN ('held','pending','read','snoozed') AND expires_at<=?",
                (self._iso(now),)).fetchall():
                db.execute("UPDATE notifications SET state='expired', terminal_note='expired' WHERE notification_id=?",
                           (row["notification_id"],))
                self._event(db, row["notification_id"], now, "expired", "passed its expiry time")
            for row in db.execute("SELECT notification_id FROM notifications WHERE state='snoozed' AND snoozed_until<=?",
                                  (self._iso(now),)).fetchall():
                db.execute("UPDATE notifications SET state='held', deliver_after=?, snoozed_until=NULL WHERE notification_id=?",
                           (self._iso(now), row["notification_id"]))
                self._event(db, row["notification_id"], now, "snooze_ended", "snooze finished; re-evaluating policy")
            due = [dict(r) for r in db.execute(
                "SELECT * FROM notifications WHERE state='held' AND deliver_after<=?", (self._iso(now),))]
            due.sort(key=lambda r: (_PRIORITY_RANK[r["priority"]], -r["relevance"], r["created_at"]))
            day_start, day_end = self._day_bounds(settings, now)
            released_today = db.execute(
                "SELECT COUNT(*) FROM notifications WHERE released_at>=? AND released_at<?",
                (self._iso(day_start), self._iso(day_end))).fetchone()[0]
            last_row = db.execute("SELECT MAX(released_at) FROM notifications").fetchone()[0]
            last_release = datetime.fromisoformat(last_row) if last_row else None
            for row in due:
                urgent = row["priority"] == "urgent"
                defer_to, why = None, ""
                if self.in_quiet_hours(settings, now) and not (urgent and settings.urgent_bypasses_quiet):
                    defer_to, why = self.quiet_end_after(settings, now), "quiet hours"
                elif not urgent and released_today >= settings.max_per_day:
                    defer_to, why = day_end, "daily limit reached"
                elif not urgent and last_release and last_release + timedelta(minutes=settings.min_gap_minutes) > now:
                    defer_to, why = last_release + timedelta(minutes=settings.min_gap_minutes), "minimum gap between notifications"
                if defer_to is not None:
                    if defer_to >= datetime.fromisoformat(row["expires_at"]):
                        db.execute("UPDATE notifications SET state='expired', terminal_note=? WHERE notification_id=?",
                                   (f"expired_before_delivery:{why}", row["notification_id"]))
                        self._event(db, row["notification_id"], now, "expired", f"would expire before delivery ({why})")
                    else:
                        db.execute("UPDATE notifications SET deliver_after=? WHERE notification_id=?",
                                   (self._iso(defer_to), row["notification_id"]))
                        self._event(db, row["notification_id"], now, "deferred", f"{why}; next try {self._iso(defer_to)}")
                    continue
                db.execute("UPDATE notifications SET state='pending', released_at=? WHERE notification_id=?",
                           (self._iso(now), row["notification_id"]))
                self._event(db, row["notification_id"], now, "delivered", "released to " + ",".join(json.loads(row["channels_json"])))
                released_today += 1
                last_release = now
            stale = db.execute(
                "SELECT notification_id FROM notifications WHERE state IN ('dismissed','expired','suppressed') "
                "ORDER BY created_at DESC LIMIT -1 OFFSET ?", (KEEP_TERMINAL_ROWS,)).fetchall()
            for row in stale:
                db.execute("DELETE FROM notification_events WHERE notification_id=?", (row["notification_id"],))
                db.execute("DELETE FROM notifications WHERE notification_id=?", (row["notification_id"],))

    # ---- reads and actions --------------------------------------------------------------------
    def list(self, *, states: tuple[str, ...] = VISIBLE_STATES, limit: int = 50, now: datetime | None = None) -> list[dict[str, Any]]:
        self.refresh(now)
        marks = ",".join("?" for _ in states)
        with self._db() as db:
            rows = db.execute(f"SELECT * FROM notifications WHERE state IN ({marks}) LIMIT 500", states).fetchall()
        items = [self._decode(r) for r in rows]
        items.sort(key=lambda i: (_PRIORITY_RANK[i["priority"]], i["released_at"] or i["created_at"]), reverse=False)
        return items[: max(1, min(limit, 200))]

    def counts(self, now: datetime | None = None) -> dict[str, int]:
        self.refresh(now)
        with self._db() as db:
            return {r["state"]: r["n"] for r in db.execute("SELECT state, COUNT(*) AS n FROM notifications GROUP BY state")}

    def _get(self, db: sqlite3.Connection, nid: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM notifications WHERE notification_id=?", (nid,)).fetchone()
        if row is None:
            raise NotificationNotFound(nid)
        return row

    def mark_read(self, nid: str, now: datetime | None = None) -> dict[str, Any]:
        now = (now or utc_now()).astimezone(UTC)
        self.refresh(now)
        with self._db() as db:
            row = self._get(db, nid)
            if row["state"] not in ("pending", "read"):
                raise ValueError(f"cannot mark a {row['state']} notification as read")
            db.execute("UPDATE notifications SET state='read', read_at=COALESCE(read_at, ?) WHERE notification_id=?",
                       (self._iso(now), nid))
            self._event(db, nid, now, "read", "owner opened it")
            return self._decode(self._get(db, nid))

    def dismiss(self, nid: str, now: datetime | None = None) -> dict[str, Any]:
        now = (now or utc_now()).astimezone(UTC)
        self.refresh(now)
        with self._db() as db:
            row = self._get(db, nid)
            if row["state"] not in OPEN_STATES:
                raise ValueError(f"cannot dismiss a {row['state']} notification")
            db.execute("UPDATE notifications SET state='dismissed', dismissed_at=? WHERE notification_id=?",
                       (self._iso(now), nid))
            self._event(db, nid, now, "dismissed", "owner dismissed it")
            return self._decode(self._get(db, nid))

    def snooze(self, nid: str, *, minutes: int | None = None, until: datetime | None = None,
               now: datetime | None = None) -> dict[str, Any]:
        now = (now or utc_now()).astimezone(UTC)
        self.refresh(now)
        if until is not None:
            if until.tzinfo is None:
                raise ValueError("snooze time must include a timezone")
            wake = until.astimezone(UTC)
        else:
            wake = now + timedelta(minutes=minutes if minutes is not None else self.get_settings().default_snooze_minutes)
        if not now < wake <= now + timedelta(minutes=MAX_SNOOZE_MINUTES):
            raise ValueError("snooze must end in the future, within 30 days")
        with self._db() as db:
            row = self._get(db, nid)
            if row["state"] not in ("pending", "read", "held"):
                raise ValueError(f"cannot snooze a {row['state']} notification")
            if wake >= datetime.fromisoformat(row["expires_at"]):
                raise ValueError("this notification would expire before the snooze ends")
            db.execute("UPDATE notifications SET state='snoozed', snoozed_until=? WHERE notification_id=?",
                       (self._iso(wake), nid))
            self._event(db, nid, now, "snoozed", f"until {self._iso(wake)}")
            return self._decode(self._get(db, nid))

    def open_for_source(self, source_type: str) -> list[dict[str, Any]]:
        """Open (held, pending, read or snoozed) notifications raised by one kind of source."""
        with self._db() as db:
            rows = db.execute("SELECT * FROM notifications WHERE source_type=? AND state IN "
                              "('held','pending','read','snoozed')", (source_type,)).fetchall()
        return [self._decode(r) for r in rows]

    def withdraw(self, nid: str, note: str, now: datetime | None = None) -> bool:
        """Retire an open notification because its source no longer needs attention.

        Unlike dismiss this is not the owner's choice, so it sets no dismiss cooldown.
        """
        now = (now or utc_now()).astimezone(UTC)
        with self._db() as db:
            row = db.execute("SELECT state FROM notifications WHERE notification_id=?", (nid,)).fetchone()
            if row is None or row["state"] not in OPEN_STATES:
                return False
            db.execute("UPDATE notifications SET state='expired', terminal_note=? WHERE notification_id=?",
                       (f"withdrawn:{note}"[:100], nid))
            self._event(db, nid, now, "withdrawn", note)
            return True

    def explain(self, nid: str, now: datetime | None = None) -> dict[str, Any]:
        self.refresh(now)
        with self._db() as db:
            item = self._decode(self._get(db, nid))
            events = [dict(r) for r in db.execute(
                "SELECT at, event, detail FROM notification_events WHERE notification_id=? ORDER BY event_id", (nid,))]
        return {
            "notification_id": nid,
            "why": item["reason"],
            "source": {"type": item["source_type"], "ref": item["source_ref"]},
            "evidence": item["evidence"],
            "state": item["state"],
            "priority": item["priority"],
            "relevance": item["relevance"],
            "occurrences": item["occurrences"],
            "channels": item["channels"],
            "expires_at": item["expires_at"],
            "history": events,
        }

    def voice_digest(self, *, limit: int = 3, now: datetime | None = None) -> list[dict[str, Any]]:
        """Items to mention at the start of a voice session; each is announced at most once."""
        now = (now or utc_now()).astimezone(UTC)
        self.refresh(now)
        if not self.get_settings().voice_enabled:
            return []
        with self._db() as db:
            rows = db.execute("SELECT * FROM notifications WHERE state='pending' AND voice_announced_at IS NULL "
                              "AND channels_json LIKE '%\"voice\"%'").fetchall()
            items = sorted((self._decode(r) for r in rows), key=lambda i: (_PRIORITY_RANK[i["priority"]], i["released_at"] or ""))
            chosen = items[: max(1, min(limit, 10))]
            for item in chosen:
                db.execute("UPDATE notifications SET voice_announced_at=? WHERE notification_id=?",
                           (self._iso(now), item["notification_id"]))
                self._event(db, item["notification_id"], now, "voice_announced", "mentioned at voice session start")
        return chosen
