"""Owner-scoped personal data: tasks, reminders and notes (MIA-LIFE-01).

Privacy contract
- Single owner, local-first: rows live in the same SQLite file as the rest of Mia's memory
  and are not encrypted at rest by this module (encrypted backup is MIA-LIFE-11).
- Nothing here is injected into model context, indexed for retrieval or sent to a provider.
  A later task must opt in explicitly; items marked ``private`` must never be.
- Every item has provenance: who created it (``owner_provided``, ``conversation_captured``
  with a source reference, or ``imported``). Capture never creates items by itself.
- A project association is optional and uses the same normalised name as conversations and
  structured records. It is a filter for the owner's views, not a security boundary.
- Credential-like text is refused. Deletion is a hard delete of the row; the audit log
  keeps ids and field names only, never titles or details.
- Retention is owner-triggered: finished (done/cancelled) items older than a period the
  owner passes can be previewed (default) and then purged. Open items are never purged.
- Export returns every item, unfiltered, so the owner can always take their data out.
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from mia.memory.safety import reject_secret
from mia.memory.schema import AuditLog

KINDS = frozenset({"task", "reminder", "note"})
STATUSES = frozenset({"open", "done", "cancelled"})
FINISHED = ("done", "cancelled")
PROVENANCE = frozenset({"owner_provided", "conversation_captured", "imported"})
MAX_TITLE = 200
MAX_DETAILS = 5_000
MAX_LIST = 500
PRIORITIES = {1: "high", 2: "normal", 3: "low"}
FREQUENCIES = frozenset({"daily", "weekly", "monthly"})
MAX_INTERVAL = 365
_MAX_ADVANCE_STEPS = 5_000


class PersonalItemNotFoundError(KeyError):
    """No personal item has this id."""


class DuplicateItemError(ValueError):
    """An open item with the same kind, title and project already exists."""

    def __init__(self, existing_id: str) -> None:
        super().__init__("an open item with the same title already exists")
        self.existing_id = existing_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_due(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ValueError("due_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("due_at must include a timezone offset (for example +05:00 or Z)")
    return parsed.astimezone(UTC).isoformat()


def _clean_priority(priority: int | None) -> int:
    if priority is None:
        return 2
    if isinstance(priority, bool) or priority not in PRIORITIES:
        raise ValueError("priority must be 1 (high), 2 (normal) or 3 (low)")
    return int(priority)


def _clean_recurrence(rule: dict[str, Any] | None) -> str | None:
    if rule is None:
        return None
    if not isinstance(rule, dict) or set(rule) - {"freq", "interval"}:
        raise ValueError('recurrence must look like {"freq": "daily|weekly|monthly", "interval": 1}')
    freq = rule.get("freq")
    interval = rule.get("interval", 1)
    if freq not in FREQUENCIES:
        raise ValueError("recurrence freq must be one of " + ", ".join(sorted(FREQUENCIES)))
    if isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= MAX_INTERVAL:
        raise ValueError(f"recurrence interval must be a whole number from 1 to {MAX_INTERVAL}")
    return json.dumps({"freq": freq, "interval": interval}, sort_keys=True)


def _dedupe_key(kind: str, title: str, project: str | None) -> str:
    return f"{kind}|{' '.join(title.split()).casefold()}|{(project or '').casefold()}"


def next_occurrence(due_utc: str, rule: dict[str, Any], tz: ZoneInfo, now: datetime) -> str:
    """First occurrence strictly after both ``due_utc`` and ``now``, in the owner's wall time.

    Wall-clock time is preserved across daylight-saving changes (a 09:00 task stays 09:00
    local); a local time that does not exist on a given day resolves to the earlier
    offset. Monthly rules clamp to the month's last day (31st -> 28th/30th) without
    drifting, because every step is computed from the original day of month.
    """
    start = datetime.fromisoformat(due_utc).astimezone(tz)
    base = start.replace(tzinfo=None)
    floor = max(datetime.fromisoformat(due_utc), now)
    interval = int(rule["interval"])
    for step in range(1, _MAX_ADVANCE_STEPS + 1):
        if rule["freq"] == "daily":
            candidate = base + timedelta(days=interval * step)
        elif rule["freq"] == "weekly":
            candidate = base + timedelta(weeks=interval * step)
        else:
            months = base.month - 1 + interval * step
            year, month = base.year + months // 12, months % 12 + 1
            day = min(base.day, calendar.monthrange(year, month)[1])
            candidate = base.replace(year=year, month=month, day=day)
        aware = candidate.replace(tzinfo=tz).astimezone(UTC)
        if aware > floor:
            return aware.isoformat()
    raise ValueError("could not compute the next occurrence")


def _clean_project(project: str | None) -> str | None:
    from mia.memory.store import normalise_project_name  # lazy: store imports this module

    return normalise_project_name(project)


def _clean_text(title: str | None, details: str | None) -> tuple[str | None, str | None]:
    if title is not None:
        title = " ".join(title.split())
        if not title:
            raise ValueError("title is required")
        if len(title) > MAX_TITLE:
            raise ValueError(f"title is limited to {MAX_TITLE} characters")
        reject_secret(title, field="title")
    if details is not None:
        details = details.strip()
        if len(details) > MAX_DETAILS:
            raise ValueError(f"details are limited to {MAX_DETAILS} characters")
        reject_secret(details, field="details")
    return title, details


class PersonalData:
    """Operations over the ``personal_items`` table of the shared memory database."""

    def __init__(
        self,
        connect: Callable[[], AbstractContextManager[sqlite3.Connection]],
        timezone: Callable[[], str] = lambda: "UTC",
    ) -> None:
        self._connect = connect
        self._timezone = timezone

    def _tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self._timezone())
        except Exception:  # a bad stored value falls back to UTC rather than breaking tasks
            return ZoneInfo("UTC")

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS personal_items (
                item_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                due_at TEXT,
                project TEXT,
                private INTEGER NOT NULL DEFAULT 0,
                provenance TEXT NOT NULL,
                source_conversation_id TEXT,
                source_message_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_personal_kind_status "
            "ON personal_items(kind, status, updated_at DESC)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS idx_personal_project ON personal_items(project)")

    # ------------------------------------------------------------------ write

    def add(
        self,
        *,
        kind: str,
        title: str,
        details: str = "",
        due_at: str | None = None,
        project: str | None = None,
        private: bool = False,
        provenance: str = "owner_provided",
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        priority: int | None = None,
        recurrence: dict[str, Any] | None = None,
        allow_duplicate: bool = False,
        actor: str = "owner",
    ) -> str:
        if kind not in KINDS:
            raise ValueError("kind must be one of " + ", ".join(sorted(KINDS)))
        if provenance not in PROVENANCE:
            raise ValueError("provenance must be one of " + ", ".join(sorted(PROVENANCE)))
        if provenance == "conversation_captured" and not (
            source_conversation_id and source_message_id
        ):
            raise ValueError("a conversation-captured item must cite its source message")
        title, details = _clean_text(title, details)
        due = _clean_due(due_at)
        if kind == "reminder" and due is None:
            raise ValueError("a reminder needs a due_at time")
        name = _clean_project(project)
        prio = _clean_priority(priority)
        rule = _clean_recurrence(recurrence)
        if rule is not None and due is None:
            raise ValueError("a recurring item needs a due_at time")
        key = _dedupe_key(kind, title, name)
        item_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as db:
            if not allow_duplicate:
                self._check_duplicate(db, key, None)
            db.execute(
                """
                INSERT INTO personal_items (
                    item_id, kind, title, details, status, due_at, project, private,
                    provenance, source_conversation_id, source_message_id,
                    created_at, updated_at, priority, recurrence, series_id, dedupe_key
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, kind, title, details or "", due, name, 1 if private else 0,
                 provenance, source_conversation_id, source_message_id, now, now,
                 prio, rule, item_id if rule else None, key),
            )
            AuditLog.write(
                db, actor=actor, action="personal_item_created",
                target_type="personal_item", target_id=item_id,
                detail={"kind": kind, "provenance": provenance, "private": bool(private)},
            )
        return item_id

    def update(
        self,
        item_id: str,
        *,
        title: str | None = None,
        details: str | None = None,
        status: str | None = None,
        due_at: str | None = None,
        clear_due: bool = False,
        project: str | None = None,
        clear_project: bool = False,
        private: bool | None = None,
        priority: int | None = None,
        recurrence: dict[str, Any] | None = None,
        clear_recurrence: bool = False,
        allow_duplicate: bool = False,
        actor: str = "owner",
    ) -> list[str]:
        """Change fields. Returns the names of the fields that changed.

        The audit log and history hold field names and structural values (status, priority,
        due time, project, recurrence), never title or details text. Completing a recurring
        item must go through :meth:`complete` so the next occurrence is created.
        """
        if status is not None and status not in STATUSES:
            raise ValueError("status must be one of " + ", ".join(sorted(STATUSES)))
        title, details = _clean_text(title, details)
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if details is not None:
            changes["details"] = details
        if status is not None:
            changes["status"] = status
        if clear_due:
            changes["due_at"] = None
        elif due_at is not None:
            changes["due_at"] = _clean_due(due_at)
        if clear_project:
            changes["project"] = None
        elif project is not None:
            changes["project"] = _clean_project(project)
        if private is not None:
            changes["private"] = 1 if private else 0
        if priority is not None:
            changes["priority"] = _clean_priority(priority)
        if clear_recurrence:
            changes["recurrence"] = None
        elif recurrence is not None:
            changes["recurrence"] = _clean_recurrence(recurrence)
        if not changes:
            raise ValueError("nothing to change")
        with self._connect() as db:
            row = self._row(db, item_id)
            final_due = changes["due_at"] if "due_at" in changes else row["due_at"]
            final_rule = changes["recurrence"] if "recurrence" in changes else row["recurrence"]
            if row["kind"] == "reminder" and final_due is None:
                raise ValueError("a reminder needs a due_at time")
            if final_rule is not None and final_due is None:
                raise ValueError("a recurring item needs a due_at time")
            if changes.get("status") == "done" and row["status"] == "open" and row["recurrence"]:
                raise ValueError("complete a recurring item with complete() so the next one is created")
            changed = [k for k, v in changes.items() if row[k] != v]
            if not changed:
                return []
            final_project = changes["project"] if "project" in changes else row["project"]
            final_title = changes["title"] if "title" in changes else row["title"]
            if ("title" in changed or "project" in changed) and not allow_duplicate:
                if row["status"] == "open":
                    self._check_duplicate(db, _dedupe_key(row["kind"], final_title, final_project), item_id)
            now = _now()
            sets = {k: changes[k] for k in changed}
            sets["updated_at"] = now
            if "title" in changed or "project" in changed:
                sets["dedupe_key"] = _dedupe_key(row["kind"], final_title, final_project)
            if "status" in changed:
                sets["completed_at"] = now if changes["status"] in FINISHED else None
                if changes["status"] == "open" and "dedupe_key" not in sets and not allow_duplicate:
                    self._check_duplicate(db, row["dedupe_key"], item_id)  # reopening
            names = ", ".join(f"{k} = ?" for k in sets)
            db.execute(
                f"UPDATE personal_items SET {names} WHERE item_id = ?",  # noqa: S608
                (*sets.values(), item_id),
            )
            self._write_history(db, item_id, actor, row, changes, changed, now)
            AuditLog.write(
                db, actor=actor, action="personal_item_updated",
                target_type="personal_item", target_id=item_id,
                detail={"fields": sorted(changed)},
            )
        return sorted(changed)

    def complete(self, item_id: str, *, actor: str = "owner") -> dict[str, Any]:
        """Mark an open item done. A recurring item spawns its next occurrence.

        The next due time is the first occurrence after both the old due time and now,
        computed in the owner's timezone, so a long-overdue daily task does not pile up
        missed copies. Completing an already-finished item changes nothing.
        """
        with self._connect() as db:
            row = self._row(db, item_id)
            if row["status"] != "open":
                return {"changed": [], "next_item_id": None}
            now = _now()
            db.execute(
                "UPDATE personal_items SET status = 'done', completed_at = ?, updated_at = ? "
                "WHERE item_id = ?",
                (now, now, item_id),
            )
            self._write_history(db, item_id, actor, row, {"status": "done"}, ["status"], now)
            AuditLog.write(
                db, actor=actor, action="personal_item_updated",
                target_type="personal_item", target_id=item_id, detail={"fields": ["status"]},
            )
            next_id = None
            if row["recurrence"] and row["due_at"]:
                due = next_occurrence(
                    row["due_at"], json.loads(row["recurrence"]), self._tz(), datetime.now(UTC)
                )
                next_id = uuid.uuid4().hex
                db.execute(
                    """
                    INSERT INTO personal_items (
                        item_id, kind, title, details, status, due_at, project, private,
                        provenance, source_conversation_id, source_message_id,
                        created_at, updated_at, priority, recurrence, series_id, dedupe_key
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (next_id, row["kind"], row["title"], row["details"], due, row["project"],
                     row["private"], row["provenance"], row["source_conversation_id"],
                     row["source_message_id"], now, now, row["priority"], row["recurrence"],
                     row["series_id"] or item_id, row["dedupe_key"]),
                )
                AuditLog.write(
                    db, actor=actor, action="personal_item_created",
                    target_type="personal_item", target_id=next_id,
                    detail={"kind": row["kind"], "provenance": row["provenance"],
                            "private": bool(row["private"]), "next_of": item_id},
                )
        return {"changed": ["status"], "next_item_id": next_id}

    def history(self, item_id: str) -> list[dict[str, Any]]:
        """Structural change history of one item, oldest first (no title/details text)."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT changed_at, actor, field, old_value, new_value "
                "FROM personal_item_history WHERE item_id = ? ORDER BY history_id",
                (item_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, item_id: str, *, actor: str = "owner") -> bool:
        """Hard-delete one item. The audit entry holds the id and kind only."""
        with self._connect() as db:
            row = db.execute(
                "SELECT kind FROM personal_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "DELETE FROM knowledge_graph_edges WHERE "
                "(from_type = 'task' AND from_id = ?) OR (to_type = 'task' AND to_id = ?)",
                (item_id, item_id),
            )
            db.execute("DELETE FROM personal_items WHERE item_id = ?", (item_id,))
            db.execute("DELETE FROM personal_item_history WHERE item_id = ?", (item_id,))
            AuditLog.write(
                db, actor=actor, action="personal_item_deleted",
                target_type="personal_item", target_id=item_id,
                detail={"kind": row["kind"]},
            )
        return True

    def purge_finished(
        self, days: int, *, dry_run: bool = True, actor: str = "owner"
    ) -> dict[str, Any]:
        """Preview (default) or delete done/cancelled items last changed over ``days`` ago."""
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 36500:
            raise ValueError("days must be a whole number from 1 to 36500")
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT item_id, kind, title, status FROM personal_items "
                "WHERE status IN ('done', 'cancelled') AND updated_at < ? "
                "ORDER BY updated_at, item_id",
                (cutoff,),
            ).fetchall()
        count = len(rows)
        if not dry_run:
            count = sum(1 for r in rows if self.delete(r["item_id"], actor=actor))
        return {
            "cutoff": cutoff, "days": days, "dry_run": dry_run, "count": count,
            "sample": [
                {"id": r["item_id"], "kind": r["kind"], "title": r["title"],
                 "status": r["status"]}
                for r in rows[:20]
            ],
        }

    # ------------------------------------------------------------------- read

    def get(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM personal_items WHERE item_id = ?", (item_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        project: str | None = None,
        unscoped: bool = False,
        priority: int | None = None,
        overdue: bool = False,
        due_before: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where, params = [], []
        if priority is not None:
            where.append("priority = ?")
            params.append(_clean_priority(priority))
        if overdue:
            where.append("status = 'open' AND due_at IS NOT NULL AND due_at < ?")
            params.append(_now())
        if due_before is not None:
            where.append("due_at IS NOT NULL AND due_at < ?")
            params.append(_clean_due(due_before))
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if status:
            where.append("status = ?")
            params.append(status)
        if project is not None:
            name = _clean_project(project)
            where.append("LOWER(project) = LOWER(?)")
            params.append(name)
        elif unscoped:
            where.append("project IS NULL")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM personal_items {clause} "  # noqa: S608 - fixed fragments
                "ORDER BY updated_at DESC, item_id LIMIT ?",
                (*params, max(1, min(int(limit), MAX_LIST))),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT kind, COUNT(*) AS n FROM personal_items GROUP BY kind"
            ).fetchall()
        return {str(r["kind"]): int(r["n"]) for r in rows}

    def export(self) -> list[dict[str, Any]]:
        """Every item, unfiltered and in a stable order, for the owner's data export."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM personal_items ORDER BY created_at, item_id"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def export_history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT item_id, changed_at, actor, field, old_value, new_value "
                "FROM personal_item_history ORDER BY history_id"
            ).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- internal

    @staticmethod
    def _check_duplicate(db: sqlite3.Connection, key: str, exclude_id: str | None) -> None:
        row = db.execute(
            "SELECT item_id FROM personal_items WHERE dedupe_key = ? AND status = 'open' "
            "AND item_id != ? LIMIT 1",
            (key, exclude_id or ""),
        ).fetchone()
        if row:
            raise DuplicateItemError(str(row["item_id"]))

    @staticmethod
    def _write_history(
        db: sqlite3.Connection, item_id: str, actor: str, row: sqlite3.Row,
        changes: dict[str, Any], changed: list[str], now: str,
    ) -> None:
        for field in changed:
            text = field in {"title", "details"}  # text is never copied into history
            db.execute(
                "INSERT INTO personal_item_history "
                "(item_id, changed_at, actor, field, old_value, new_value) VALUES (?,?,?,?,?,?)",
                (item_id, now, actor, field,
                 None if text else (None if row[field] is None else str(row[field])),
                 None if text else (None if changes[field] is None else str(changes[field]))),
            )

    @staticmethod
    def _row(db: sqlite3.Connection, item_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM personal_items WHERE item_id = ?", (item_id,)
        ).fetchone()
        if row is None:
            raise PersonalItemNotFoundError(item_id)
        return row

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["private"] = bool(result["private"])
        result["recurrence"] = json.loads(result["recurrence"]) if result.get("recurrence") else None
        result.pop("dedupe_key", None)
        return result
