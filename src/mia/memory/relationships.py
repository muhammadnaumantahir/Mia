"""Relationship memory: typed links between structured records.

Links connect people/entities, projects, decisions, goals, episodes and facts so
Mia can follow "what did we decide for that project?" across records. A link can be
marked ``private``: it is visible to the owner but never placed in a prompt.
Links are removed with either endpoint when a record is deleted.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from mia.memory.safety import reject_secret
from mia.memory.schema import AuditLog

RELATIONS = frozenset(
    {
        "related_to",
        "part_of",
        "works_on",
        "involves",
        "decided_in",
        "depends_on",
        "blocks",
        "caused_by",
        "follows",
        "owned_by",
    }
)
VISIBILITIES = frozenset({"normal", "private"})
MAX_NOTE = 300


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RelationshipNotFoundError(KeyError):
    """No relationship has that id."""


class RelationshipStore:
    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    def add(
        self,
        from_id: str,
        to_id: str,
        relation: str,
        *,
        visibility: str = "normal",
        note: str = "",
        actor: str = "owner",
    ) -> tuple[str, bool]:
        """Create a link. Returns (relationship_id, created). A repeat is a no-op."""
        if relation not in RELATIONS:
            raise ValueError("relation must be one of " + ", ".join(sorted(RELATIONS)))
        if visibility not in VISIBILITIES:
            raise ValueError("visibility must be normal or private")
        if from_id == to_id:
            raise ValueError("a memory cannot be related to itself")
        note = (note or "").strip()
        if len(note) > MAX_NOTE:
            raise ValueError(f"note is limited to {MAX_NOTE} characters")
        if note:
            reject_secret(note, field="note")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for record_id in (from_id, to_id):
                if not db.execute(
                    "SELECT 1 FROM structured_memory WHERE record_id = ?", (record_id,)
                ).fetchone():
                    raise KeyError(record_id)
            existing = db.execute(
                "SELECT relationship_id FROM memory_relationships "
                "WHERE from_id = ? AND to_id = ? AND relation = ?",
                (from_id, to_id, relation),
            ).fetchone()
            if existing:
                return str(existing["relationship_id"]), False
            relationship_id = uuid.uuid4().hex
            db.execute(
                """
                INSERT INTO memory_relationships
                    (relationship_id, from_id, to_id, relation, visibility, note, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (relationship_id, from_id, to_id, relation, visibility, note, actor, _now()),
            )
            AuditLog.write(
                db,
                actor=actor,
                action="relationship_created",
                target_type="memory_relationship",
                target_id=relationship_id,
                detail={
                    "from_id": from_id,
                    "to_id": to_id,
                    "relation": relation,
                    "visibility": visibility,
                },
            )
        return relationship_id, True

    def set_visibility(self, relationship_id: str, visibility: str, *, actor: str = "owner") -> None:
        if visibility not in VISIBILITIES:
            raise ValueError("visibility must be normal or private")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE memory_relationships SET visibility = ? WHERE relationship_id = ?",
                (visibility, relationship_id),
            )
            if cursor.rowcount == 0:
                raise RelationshipNotFoundError(relationship_id)
            AuditLog.write(
                db,
                actor=actor,
                action="relationship_visibility_changed",
                target_type="memory_relationship",
                target_id=relationship_id,
                detail={"visibility": visibility},
            )

    def remove(self, relationship_id: str, *, actor: str = "owner") -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM memory_relationships WHERE relationship_id = ?", (relationship_id,)
            )
            if cursor.rowcount:
                AuditLog.write(
                    db,
                    actor=actor,
                    action="relationship_deleted",
                    target_type="memory_relationship",
                    target_id=relationship_id,
                    detail={},
                )
        return cursor.rowcount > 0

    def for_record(self, record_id: str, *, include_private: bool = True) -> list[dict[str, Any]]:
        clause = "" if include_private else " AND visibility = 'normal'"
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_relationships WHERE (from_id = ? OR to_id = ?)"
                f"{clause} ORDER BY created_at",  # noqa: S608 - fixed clause only
                (record_id, record_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def neighbours(self, record_ids: Iterable[str], *, limit: int = 20) -> list[dict[str, Any]]:
        """Non-private links touching any of the records, with the other end's title.

        Only links whose other end is itself retrievable (active/completed) are
        returned, so a link never reveals an archived, outdated or pending memory.
        """
        ids = [str(record_id) for record_id in record_ids]
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT r.*, a.title AS from_title, b.title AS to_title
                FROM memory_relationships r
                JOIN structured_memory a ON a.record_id = r.from_id
                JOIN structured_memory b ON b.record_id = r.to_id
                WHERE r.visibility = 'normal'
                  AND (r.from_id IN ({marks}) OR r.to_id IN ({marks}))
                  AND a.status IN ('active', 'completed')
                  AND b.status IN ('active', 'completed')
                ORDER BY r.created_at DESC
                LIMIT ?
                """,  # noqa: S608 - placeholders only
                (*ids, *ids, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def export(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_relationships ORDER BY created_at, relationship_id"
            ).fetchall()
        return [dict(row) for row in rows]
