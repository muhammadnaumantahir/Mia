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

import sqlite3
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from mia.memory.safety import reject_secret
from mia.memory.schema import AuditLog

KINDS = frozenset({"task", "reminder", "note"})
STATUSES = frozenset({"open", "done", "cancelled"})
FINISHED = ("done", "cancelled")
PROVENANCE = frozenset({"owner_provided", "conversation_captured", "imported"})
MAX_TITLE = 200
MAX_DETAILS = 5_000
MAX_LIST = 500


class PersonalItemNotFoundError(KeyError):
    """No personal item has this id."""


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
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

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
        item_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO personal_items (
                    item_id, kind, title, details, status, due_at, project, private,
                    provenance, source_conversation_id, source_message_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (item_id, kind, title, details or "", due, name, 1 if private else 0,
                 provenance, source_conversation_id, source_message_id, now, now),
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
        actor: str = "owner",
    ) -> list[str]:
        """Change fields. Returns the names of the fields that changed (audited, no text)."""
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
        if not changes:
            raise ValueError("nothing to change")
        with self._connect() as db:
            row = self._row(db, item_id)
            final_kind = row["kind"]
            final_due = changes["due_at"] if "due_at" in changes else row["due_at"]
            if final_kind == "reminder" and final_due is None:
                raise ValueError("a reminder needs a due_at time")
            changed = [k for k, v in changes.items() if row[k] != v]
            if not changed:
                return []
            now = _now()
            sets = {**{k: changes[k] for k in changed}, "updated_at": now}
            if "status" in changed:
                sets["completed_at"] = now if changes["status"] in FINISHED else None
            names = ", ".join(f"{k} = ?" for k in sets)
            db.execute(
                f"UPDATE personal_items SET {names} WHERE item_id = ?",  # noqa: S608
                (*sets.values(), item_id),
            )
            AuditLog.write(
                db, actor=actor, action="personal_item_updated",
                target_type="personal_item", target_id=item_id,
                detail={"fields": sorted(changed)},
            )
        return sorted(changed)

    def delete(self, item_id: str, *, actor: str = "owner") -> bool:
        """Hard-delete one item. The audit entry holds the id and kind only."""
        with self._connect() as db:
            row = db.execute(
                "SELECT kind FROM personal_items WHERE item_id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM personal_items WHERE item_id = ?", (item_id,))
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
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        where, params = [], []
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

    # --------------------------------------------------------------- internal

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
        return result
