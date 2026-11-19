"""Structured, provenance-aware memory with a full lifecycle.

Record types: decision, goal, episode, project, entity (people/things), plus the
semantic types fact, preference and lesson. Records carry provenance, optional
confidence, importance, an origin (which conversation/message and capture mode
produced them) and a lifecycle status.

Statuses
    pending     suggested by capture, waiting for the owner; never retrieved
    active      in use
    completed   finished goal/project; still retrievable
    outdated    no longer true but kept for history; never retrieved
    archived    set aside by the owner; never retrieved, restorable
    expired     a temporary memory past its expiry; never retrieved
    superseded  replaced by a correction (the successor is linked)
    rejected    declined by the owner (final)

Only ``active`` and ``completed`` records are indexed for retrieval. Leaving those
states removes the search-index row in the same transaction; deleting a record
removes its index row, edit history, relationships and usage rows too.

Corrections never overwrite: a correction creates a successor and keeps the original.
Decisions cannot be edited in place at all (only corrected), so a historical
decision is never silently rewritten. Other types may be edited in place, but the
previous version is snapshotted to ``structured_memory_history`` first. Every state
change writes an audit entry in the same transaction.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from mia.memory.safety import reject_secret
from mia.memory.schema import AuditLog

RECORD_TYPES = frozenset(
    {"decision", "goal", "episode", "project", "entity", "fact", "preference", "lesson"}
)
STATUSES = frozenset(
    {
        "pending",
        "active",
        "completed",
        "outdated",
        "archived",
        "expired",
        "superseded",
        "rejected",
    }
)
RETRIEVABLE = ("active", "completed")
PROVENANCE = frozenset(
    {"owner_provided", "observed", "source_backed", "inferred", "hypothesis", "validated"}
)
EVIDENCE_REQUIRED = frozenset({"inferred", "validated", "source_backed"})
EVIDENCE_TYPES = frozenset({"message", "knowledge", "record", "url", "tool_event", "other"})
ACTORS = frozenset({"owner", "capture", "maintenance", "system", "import"})

MAX_TITLE = 200
MAX_BODY = 20_000
MAX_EVIDENCE = 20
MAX_DETAILS_BYTES = 20_000
MAX_ORIGIN_BYTES = 4_000
DEFAULT_IMPORTANCE = 0.5


class RecordNotFoundError(KeyError):
    """No structured memory record has that id."""


class RecordStateError(ValueError):
    """The record's current state does not allow the requested change."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def normalise_title(title: str) -> str:
    return re.sub(r"\W+", " ", title.lower()).strip()


def _clean_evidence(evidence: Iterable[dict[str, Any]] | None) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for entry in evidence or []:
        if not isinstance(entry, dict):
            raise ValueError("evidence entries must be objects with type and ref")
        kind = str(entry.get("type", "")).strip()
        ref = str(entry.get("ref", "")).strip()
        if kind not in EVIDENCE_TYPES or not ref:
            raise ValueError(
                "evidence needs a type (" + ", ".join(sorted(EVIDENCE_TYPES)) + ") and a ref"
            )
        cleaned.append({"type": kind, "ref": ref[:500]})
    if len(cleaned) > MAX_EVIDENCE:
        raise ValueError(f"at most {MAX_EVIDENCE} evidence references")
    return cleaned


def _clean_confidence(confidence: float | None) -> float | None:
    if confidence is None:
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number between 0 and 1")
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be a finite number between 0 and 1")
    return float(confidence)


def _clean_importance(importance: float | None) -> float:
    if importance is None:
        return DEFAULT_IMPORTANCE
    if isinstance(importance, bool) or not isinstance(importance, (int, float)):
        raise ValueError("importance must be a number between 0 and 1")
    if not math.isfinite(importance) or not 0.0 <= importance <= 1.0:
        raise ValueError("importance must be a finite number between 0 and 1")
    return float(importance)


def _clean_expiry(expires_at: str | None) -> str | None:
    if expires_at is None or expires_at == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _clean_origin(origin: dict[str, Any] | None) -> dict[str, Any]:
    origin = origin or {}
    if not isinstance(origin, dict):
        raise ValueError("origin must be an object")
    encoded = json.dumps(origin, ensure_ascii=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > MAX_ORIGIN_BYTES:
        raise ValueError("origin exceeds the 4 KB limit")
    return origin


def _check_content(title: str, body: str, details: dict[str, Any] | None) -> None:
    reject_secret(title, field="title")
    reject_secret(body, field="body")
    if details:
        reject_secret(json.dumps(details, ensure_ascii=False), field="details")


class StructuredMemory:
    """Operations over the ``structured_memory`` table of the shared memory database."""

    def __init__(self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]) -> None:
        self._connect = connect

    @staticmethod
    def initialize(db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS structured_memory (
                record_id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                title TEXT NOT NULL,
                title_key TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL,
                provenance TEXT NOT NULL,
                confidence REAL,
                evidence_json TEXT NOT NULL,
                details_json TEXT NOT NULL,
                supersedes_id TEXT,
                superseded_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolved_at TEXT
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_structured_type_status
                ON structured_memory(record_type, status, updated_at DESC)
        """)
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS structured_fts
            USING fts5(record_id UNINDEXED, title, body)
        """)

    # ------------------------------------------------------------------ write

    def add(
        self,
        *,
        record_type: str,
        title: str,
        body: str,
        provenance: str = "owner_provided",
        confidence: float | None = None,
        evidence: Iterable[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
        status: str = "active",
        importance: float | None = None,
        temporary: bool = False,
        expires_at: str | None = None,
        origin: dict[str, Any] | None = None,
        actor: str = "owner",
    ) -> tuple[str, list[str]]:
        """Create a record. Returns (record_id, ids of competing active records)."""
        with self._connect() as db:
            return self._insert(
                db,
                record_type=record_type,
                title=title,
                body=body,
                provenance=provenance,
                confidence=confidence,
                evidence=evidence,
                details=details,
                status=status,
                supersedes_id=None,
                importance=importance,
                temporary=temporary,
                expires_at=expires_at,
                origin=origin,
                actor=actor,
            )

    def supersede(
        self,
        record_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        details: dict[str, Any] | None = None,
        provenance: str = "owner_provided",
        confidence: float | None = None,
        evidence: Iterable[dict[str, Any]] | None = None,
        actor: str = "owner",
    ) -> tuple[str, list[str]]:
        """Owner correction: create a successor and retire the old record, atomically."""
        if title is None and body is None and details is None:
            raise ValueError("a correction must change the title, body or details")
        with self._connect() as db:
            old = self._row(db, record_id)
            if old["status"] not in {"active", "completed", "outdated"}:
                raise RecordStateError(
                    f"record is {old['status']} and cannot be corrected; "
                    "correct its current successor instead"
                )
            links = _clean_evidence(evidence)
            if provenance in EVIDENCE_REQUIRED and not links:
                # the automatic link to the original must not stand in for real evidence
                raise ValueError(f"{provenance} records must cite at least one evidence reference")
            links.append({"type": "record", "ref": record_id})  # keep the link to the original
            new_id, conflicts = self._insert(
                db,
                record_type=old["record_type"],
                title=title if title is not None else old["title"],
                body=body if body is not None else old["body"],
                provenance=provenance,
                confidence=confidence,
                evidence=links,
                details=details if details is not None else json.loads(old["details_json"]),
                status="active",
                supersedes_id=record_id,
                ignore_ids={record_id},
                importance=old["importance"],
                temporary=bool(old["temporary"]),
                expires_at=old["expires_at"],
                origin={"corrects": record_id},
                actor=actor,
                audit_action="record_corrected",
            )
            now = _now()
            db.execute(
                """
                UPDATE structured_memory
                SET status = 'superseded', superseded_by = ?, updated_at = ?, resolved_at = ?
                WHERE record_id = ?
                """,
                (new_id, now, now, record_id),
            )
            db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))
            AuditLog.write(
                db,
                actor=actor,
                action="record_superseded",
                target_type="memory_record",
                target_id=record_id,
                detail={"superseded_by": new_id, "was_status": old["status"]},
            )
        return new_id, conflicts

    def set_status(self, record_id: str, status: str, *, actor: str = "owner") -> None:
        """active <-> completed, or reject an active/completed/pending record (final)."""
        if status not in {"active", "completed", "rejected"}:
            raise ValueError("status must be active, completed or rejected")
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] in {"superseded", "rejected"}:
                raise RecordStateError(f"record is {row['status']} and cannot change status")
            if row["status"] == "pending" and status != "rejected":
                raise RecordStateError("a pending suggestion must be approved or rejected")
            if row["status"] in {"outdated", "archived", "expired"} and status != "rejected":
                raise RecordStateError(
                    f"record is {row['status']}; restore it first (reactivate or unarchive)"
                )
            self._snapshot(db, row, actor, f"status:{status}")
            now = _now()
            db.execute(
                "UPDATE structured_memory SET status = ?, updated_at = ?, resolved_at = ? "
                "WHERE record_id = ?",
                (status, now, None if status == "active" else now, record_id),
            )
            if status == "rejected":
                db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))
            AuditLog.write(
                db,
                actor=actor,
                action="record_status_changed",
                target_type="memory_record",
                target_id=record_id,
                detail={"from": row["status"], "to": status},
            )

    def approve(self, record_id: str, *, actor: str = "owner") -> list[str]:
        """Accept a pending suggestion: it becomes an owner-confirmed active record.

        Returns ids of competing active records so the owner can be told.
        """
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] != "pending":
                raise RecordStateError(f"record is {row['status']}, not pending")
            self._snapshot(db, row, actor, "approve")
            origin = json.loads(row["origin_json"])
            origin.update({"approved_at": _now(), "provenance_before_approval": row["provenance"]})
            now = _now()
            db.execute(
                """
                UPDATE structured_memory
                SET status = 'active', provenance = 'owner_provided', origin_json = ?,
                    updated_at = ?, resolved_at = NULL
                WHERE record_id = ?
                """,
                (json.dumps(origin, ensure_ascii=False, sort_keys=True), now, record_id),
            )
            self._index(db, record_id, row["title"], row["body"])
            competing = self._competing(db, row["record_type"], row["title_key"], {record_id})
            AuditLog.write(
                db,
                actor=actor,
                action="record_approved",
                target_type="memory_record",
                target_id=record_id,
                detail={"conflicts_with": competing},
            )
        return competing

    def confirm(self, record_id: str, *, actor: str = "owner") -> None:
        """The owner confirms an inferred/observed/hypothesis record is true."""
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] in {"superseded", "rejected", "expired"}:
                raise RecordStateError(f"record is {row['status']} and cannot be confirmed")
            if row["provenance"] == "owner_provided":
                return
            self._snapshot(db, row, actor, "confirm")
            origin = json.loads(row["origin_json"])
            origin.update({"confirmed_at": _now(), "provenance_before_confirmation": row["provenance"]})
            db.execute(
                "UPDATE structured_memory SET provenance = 'owner_provided', origin_json = ?, "
                "updated_at = ? WHERE record_id = ?",
                (json.dumps(origin, ensure_ascii=False, sort_keys=True), _now(), record_id),
            )
            AuditLog.write(
                db,
                actor=actor,
                action="record_confirmed",
                target_type="memory_record",
                target_id=record_id,
                detail={"provenance_before": row["provenance"]},
            )

    def edit(
        self,
        record_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        details: dict[str, Any] | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        actor: str = "owner",
    ) -> list[str]:
        """Edit in place after snapshotting the previous version.

        Decisions are never edited in place (use ``supersede``); only importance may
        change on one. An owner edit of content makes the record owner-confirmed.
        Returns ids of competing active records created by a title change.
        """
        content_change = any(value is not None for value in (title, body, details))
        if not content_change and confidence is None and importance is None:
            raise ValueError("nothing to change")
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] in {"superseded", "rejected", "expired"}:
                raise RecordStateError(f"record is {row['status']} and cannot be edited")
            if row["record_type"] == "decision" and content_change:
                raise RecordStateError(
                    "decisions are corrected, not edited, so the original is preserved; "
                    "use a correction"
                )
            new_title = (title if title is not None else row["title"]).strip()
            new_body = (body if body is not None else row["body"]).strip()
            new_details = details if details is not None else json.loads(row["details_json"])
            if not new_title or not new_body:
                raise ValueError("title and body are required")
            if len(new_title) > MAX_TITLE or len(new_body) > MAX_BODY:
                raise ValueError(
                    f"title is limited to {MAX_TITLE} and body to {MAX_BODY} characters"
                )
            _check_content(new_title, new_body, new_details if details is not None else None)
            details_json = json.dumps(new_details, ensure_ascii=False, sort_keys=True)
            if len(details_json.encode("utf-8")) > MAX_DETAILS_BYTES:
                raise ValueError("details exceed the 20 KB limit")
            new_confidence = (
                _clean_confidence(confidence) if confidence is not None else row["confidence"]
            )
            new_importance = (
                _clean_importance(importance) if importance is not None else row["importance"]
            )
            self._snapshot(db, row, actor, "edit")
            key = normalise_title(new_title)
            provenance = "owner_provided" if content_change and actor == "owner" else row["provenance"]
            db.execute(
                """
                UPDATE structured_memory
                SET title = ?, title_key = ?, body = ?, details_json = ?, confidence = ?,
                    importance = ?, provenance = ?, updated_at = ?
                WHERE record_id = ?
                """,
                (
                    new_title, key, new_body, details_json, new_confidence,
                    new_importance, provenance, _now(), record_id,
                ),
            )
            if row["status"] in RETRIEVABLE:
                db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))
                self._index(db, record_id, new_title, new_body)
            competing = (
                self._competing(db, row["record_type"], key, {record_id})
                if row["status"] == "active" else []
            )
            AuditLog.write(
                db,
                actor=actor,
                action="record_edited",
                target_type="memory_record",
                target_id=record_id,
                detail={
                    "changed": sorted(
                        name for name, value in (
                            ("title", title), ("body", body), ("details", details),
                            ("confidence", confidence), ("importance", importance),
                        ) if value is not None
                    ),
                    "conflicts_with": competing,
                },
            )
        return competing

    def archive(self, record_id: str, *, actor: str = "owner") -> None:
        self._leave_retrieval(record_id, "archived", actor, {"active", "completed", "outdated"})

    def mark_outdated(self, record_id: str, *, actor: str = "owner") -> None:
        self._leave_retrieval(record_id, "outdated", actor, {"active", "completed"})

    def restore(self, record_id: str, *, actor: str = "owner") -> list[str]:
        """Bring an archived or outdated record back to active. Returns competing ids."""
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] not in {"archived", "outdated"}:
                raise RecordStateError(f"record is {row['status']}, not archived or outdated")
            self._snapshot(db, row, actor, "restore")
            db.execute(
                "UPDATE structured_memory SET status = 'active', archived_at = NULL, "
                "resolved_at = NULL, updated_at = ? WHERE record_id = ?",
                (_now(), record_id),
            )
            self._index(db, record_id, row["title"], row["body"])
            competing = self._competing(db, row["record_type"], row["title_key"], {record_id})
            AuditLog.write(
                db,
                actor=actor,
                action="record_restored",
                target_type="memory_record",
                target_id=record_id,
                detail={"from": row["status"], "conflicts_with": competing},
            )
        return competing

    def set_temporary(
        self,
        record_id: str,
        *,
        temporary: bool,
        expires_at: str | None = None,
        actor: str = "owner",
    ) -> None:
        """Mark a memory temporary (with an expiry) or make it permanent again."""
        expiry = _clean_expiry(expires_at) if temporary else None
        if temporary and expiry is None:
            raise ValueError("a temporary memory needs an expires_at timestamp")
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] in {"superseded", "rejected", "expired"}:
                raise RecordStateError(f"record is {row['status']} and cannot be changed")
            self._snapshot(db, row, actor, "temporary")
            db.execute(
                "UPDATE structured_memory SET temporary = ?, expires_at = ?, updated_at = ? "
                "WHERE record_id = ?",
                (1 if temporary else 0, expiry, _now(), record_id),
            )
            AuditLog.write(
                db,
                actor=actor,
                action="record_temporary_set" if temporary else "record_made_permanent",
                target_type="memory_record",
                target_id=record_id,
                detail={"expires_at": expiry},
            )

    def expire_due(self, *, now: str | None = None, actor: str = "maintenance") -> list[str]:
        """Expire temporary memories past their expiry; they stop being retrieved."""
        cutoff = _clean_expiry(now) if now else _now()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM structured_memory
                WHERE temporary = 1 AND expires_at IS NOT NULL AND expires_at <= ?
                  AND status IN ('active', 'completed', 'pending', 'outdated')
                """,
                (cutoff,),
            ).fetchall()
            expired: list[str] = []
            for row in rows:
                self._snapshot(db, row, actor, "expire")
                db.execute(
                    "UPDATE structured_memory SET status = 'expired', resolved_at = ?, "
                    "updated_at = ? WHERE record_id = ?",
                    (cutoff, _now(), row["record_id"]),
                )
                db.execute("DELETE FROM structured_fts WHERE record_id = ?", (row["record_id"],))
                AuditLog.write(
                    db,
                    actor=actor,
                    action="record_expired",
                    target_type="memory_record",
                    target_id=row["record_id"],
                    detail={"expires_at": row["expires_at"]},
                )
                expired.append(row["record_id"])
        return expired

    def expire_pending(self, *, older_than_days: int, actor: str = "maintenance") -> list[str]:
        """Drop stale capture suggestions the owner never reviewed (they become expired)."""
        if older_than_days < 1:
            raise ValueError("older_than_days must be at least 1")
        cutoff = datetime.now(UTC).timestamp() - older_than_days * 86400
        cutoff_iso = datetime.fromtimestamp(cutoff, UTC).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM structured_memory WHERE status = 'pending' AND created_at < ?",
                (cutoff_iso,),
            ).fetchall()
            for row in rows:
                db.execute(
                    "UPDATE structured_memory SET status = 'expired', resolved_at = ?, "
                    "updated_at = ? WHERE record_id = ?",
                    (_now(), _now(), row["record_id"]),
                )
                AuditLog.write(
                    db,
                    actor=actor,
                    action="record_expired",
                    target_type="memory_record",
                    target_id=row["record_id"],
                    detail={"reason": "pending suggestion not reviewed in time"},
                )
        return [str(row["record_id"]) for row in rows]

    def mark_used(self, record_ids: Iterable[str]) -> None:
        ids = [str(record_id) for record_id in record_ids]
        if not ids:
            return
        now = _now()
        with self._connect() as db:
            db.executemany(
                "UPDATE structured_memory SET use_count = use_count + 1, last_used_at = ? "
                "WHERE record_id = ?",
                [(now, record_id) for record_id in ids],
            )

    def delete(self, record_id: str, *, chain: bool = True, actor: str = "owner") -> bool:
        """Delete a record and everything derived from it.

        By default the whole correction chain goes too, so earlier wording of a
        deleted memory cannot linger as a superseded copy. Edit-history snapshots,
        relationships, usage rows and search-index rows are removed with it. Audit
        entries keep only ids and counts, never the deleted text.
        """
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM structured_memory WHERE record_id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return False
            ids = self._chain_ids(db, record_id) if chain else {record_id}
            for target in ids:
                db.execute("DELETE FROM structured_fts WHERE record_id = ?", (target,))
                db.execute("DELETE FROM structured_memory_history WHERE record_id = ?", (target,))
                db.execute(
                    "DELETE FROM memory_relationships WHERE from_id = ? OR to_id = ?",
                    (target, target),
                )
                db.execute(
                    "DELETE FROM memory_usage WHERE ref_type = 'record' AND ref_id = ?", (target,)
                )
                db.execute("DELETE FROM structured_memory WHERE record_id = ?", (target,))
            if not chain:  # keep neighbours' pointers from dangling
                db.execute(
                    "UPDATE structured_memory SET superseded_by = NULL WHERE superseded_by = ?",
                    (record_id,),
                )
                db.execute(
                    "UPDATE structured_memory SET supersedes_id = NULL WHERE supersedes_id = ?",
                    (record_id,),
                )
            AuditLog.write(
                db,
                actor=actor,
                action="record_deleted",
                target_type="memory_record",
                target_id=record_id,
                detail={"deleted_ids": sorted(ids), "record_type": row["record_type"]},
            )
        return True

    # ------------------------------------------------------------------- read

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM structured_memory WHERE record_id = ?", (record_id,)
            ).fetchone()
        return self._decode(row) if row else None

    def edit_history(self, record_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Snapshots of earlier versions of one record, newest first."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM structured_memory_history WHERE record_id = ? "
                "ORDER BY history_id DESC LIMIT ?",
                (record_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            {
                "history_id": row["history_id"],
                "changed_at": row["changed_at"],
                "actor": row["actor"],
                "change_type": row["change_type"],
                "snapshot": json.loads(row["snapshot_json"]),
            }
            for row in rows
        ]

    def history(self, record_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Correction chain containing the record, oldest first."""
        with self._connect() as db:
            current = self._row(db, record_id)
            chain = [current]
            seen = {current["record_id"]}
            node = current
            while node["supersedes_id"] and len(chain) < limit:  # walk back
                prev = db.execute(
                    "SELECT * FROM structured_memory WHERE record_id = ?",
                    (node["supersedes_id"],),
                ).fetchone()
                if not prev or prev["record_id"] in seen:
                    break
                chain.insert(0, prev)
                seen.add(prev["record_id"])
                node = prev
            node = current
            while node["superseded_by"] and len(chain) < limit:  # walk forward
                nxt = db.execute(
                    "SELECT * FROM structured_memory WHERE record_id = ?",
                    (node["superseded_by"],),
                ).fetchone()
                if not nxt or nxt["record_id"] in seen:
                    break
                chain.append(nxt)
                seen.add(nxt["record_id"])
                node = nxt
        return [self._decode(row) for row in chain]

    def list(
        self,
        *,
        record_type: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Owner listing across every status. ``query`` is a plain substring search."""
        clauses: list[str] = []
        params: list[Any] = []
        if record_type:
            clauses.append("record_type = ?")
            params.append(record_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if query and query.strip():
            like = "%" + re.sub(r"([\\%_])", r"\\\1", query.strip().lower()) + "%"
            clauses.append("(LOWER(title) LIKE ? ESCAPE '\\' OR LOWER(body) LIKE ? ESCAPE '\\')")
            params.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM structured_memory {where} "  # noqa: S608 - fixed clauses only
                "ORDER BY updated_at DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS n FROM structured_memory GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def retrieve(self, match_query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Active/completed, unexpired records matching a prepared FTS query, best first."""
        if not match_query:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.*, bm25(structured_fts) AS relevance
                FROM structured_fts
                JOIN structured_memory s ON s.record_id = structured_fts.record_id
                WHERE structured_fts MATCH ?
                  AND s.status IN ('active', 'completed')
                  AND s.archived_at IS NULL
                  AND (s.expires_at IS NULL OR s.expires_at > ?)
                ORDER BY relevance
                LIMIT ?
                """,
                (match_query, _now(), max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def source_message_ids(self, record_id: str, *, chain: bool = True) -> list[str]:
        """Chat messages that records in this record's correction chain were captured from."""
        with self._connect() as db:
            if db.execute(
                "SELECT 1 FROM structured_memory WHERE record_id = ?", (record_id,)
            ).fetchone() is None:
                return []
            ids = self._chain_ids(db, record_id) if chain else {record_id}
            found: list[str] = []
            for target in sorted(ids):
                row = db.execute(
                    "SELECT json_extract(origin_json, '$.message_id') AS mid "
                    "FROM structured_memory WHERE record_id = ?",
                    (target,),
                ).fetchone()
                if row and row["mid"] and row["mid"] not in found:
                    found.append(str(row["mid"]))
        return found

    def find_by_message(self, message_id: str) -> bool:
        """True if any record was captured from this chat message (retry safety)."""
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM structured_memory "
                "WHERE json_extract(origin_json, '$.message_id') = ? LIMIT 1",
                (message_id,),
            ).fetchone()
        return row is not None

    def active_with_title_key(self, record_type: str, title_key: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_id FROM structured_memory "
                "WHERE record_type = ? AND title_key = ? AND status = 'active'",
                (record_type, title_key),
            ).fetchall()
        return [str(row["record_id"]) for row in rows]

    def export(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM structured_memory ORDER BY created_at, record_id"
            ).fetchall()
        return [self._decode(row) for row in rows]

    # --------------------------------------------------------------- internal

    def _leave_retrieval(
        self, record_id: str, new_status: str, actor: str, allowed_from: set[str]
    ) -> None:
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] not in allowed_from:
                raise RecordStateError(f"record is {row['status']} and cannot become {new_status}")
            self._snapshot(db, row, actor, new_status)
            now = _now()
            db.execute(
                "UPDATE structured_memory SET status = ?, archived_at = ?, resolved_at = ?, "
                "updated_at = ? WHERE record_id = ?",
                (new_status, now if new_status == "archived" else None, now, now, record_id),
            )
            db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))
            AuditLog.write(
                db,
                actor=actor,
                action=f"record_{new_status}",
                target_type="memory_record",
                target_id=record_id,
                detail={"from": row["status"]},
            )

    @staticmethod
    def _index(db: sqlite3.Connection, record_id: str, title: str, body: str) -> None:
        db.execute(
            "INSERT INTO structured_fts (record_id, title, body) VALUES (?, ?, ?)",
            (record_id, title, body),
        )

    @staticmethod
    def _competing(
        db: sqlite3.Connection, record_type: str, title_key: str, ignore_ids: set[str]
    ) -> list[str]:
        return [
            row["record_id"]
            for row in db.execute(
                "SELECT record_id FROM structured_memory "
                "WHERE record_type = ? AND title_key = ? AND status = 'active'",
                (record_type, title_key),
            ).fetchall()
            if row["record_id"] not in ignore_ids
        ]

    @staticmethod
    def _chain_ids(db: sqlite3.Connection, record_id: str) -> set[str]:
        ids = {record_id}
        frontier = [record_id]
        while frontier:
            current = frontier.pop()
            rows = db.execute(
                "SELECT record_id FROM structured_memory "
                "WHERE supersedes_id = ? OR superseded_by = ? OR record_id IN ("
                "SELECT supersedes_id FROM structured_memory WHERE record_id = ?) "
                "OR record_id IN (SELECT superseded_by FROM structured_memory WHERE record_id = ?)",
                (current, current, current, current),
            ).fetchall()
            for row in rows:
                if row["record_id"] not in ids:
                    ids.add(row["record_id"])
                    frontier.append(row["record_id"])
        return ids

    @staticmethod
    def _snapshot(db: sqlite3.Connection, row: sqlite3.Row, actor: str, change_type: str) -> None:
        snapshot = {
            key: row[key]
            for key in (
                "record_type", "title", "body", "status", "provenance", "confidence",
                "importance", "temporary", "expires_at", "details_json", "origin_json",
            )
        }
        db.execute(
            "INSERT INTO structured_memory_history "
            "(record_id, changed_at, actor, change_type, snapshot_json) VALUES (?, ?, ?, ?, ?)",
            (
                row["record_id"],
                _now(),
                actor,
                change_type,
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _insert(
        self,
        db: sqlite3.Connection,
        *,
        record_type: str,
        title: str,
        body: str,
        provenance: str,
        confidence: float | None,
        evidence: Iterable[dict[str, Any]] | None,
        details: dict[str, Any] | None,
        status: str,
        supersedes_id: str | None,
        ignore_ids: set[str] | None = None,
        importance: float | None = None,
        temporary: bool = False,
        expires_at: str | None = None,
        origin: dict[str, Any] | None = None,
        actor: str = "owner",
        audit_action: str = "record_created",
    ) -> tuple[str, list[str]]:
        if record_type not in RECORD_TYPES:
            raise ValueError("record_type must be one of " + ", ".join(sorted(RECORD_TYPES)))
        if provenance not in PROVENANCE:
            raise ValueError("provenance must be one of " + ", ".join(sorted(PROVENANCE)))
        if status not in {"active", "completed", "pending"}:
            raise ValueError("new records must be active, completed or pending")
        if actor not in ACTORS:
            raise ValueError("actor must be one of " + ", ".join(sorted(ACTORS)))
        title = title.strip()
        body = body.strip()
        if not title or not body:
            raise ValueError("title and body are required")
        if len(title) > MAX_TITLE or len(body) > MAX_BODY:
            raise ValueError(f"title is limited to {MAX_TITLE} and body to {MAX_BODY} characters")
        _check_content(title, body, details)
        links = _clean_evidence(evidence)
        if provenance in EVIDENCE_REQUIRED and not links:
            raise ValueError(f"{provenance} records must cite at least one evidence reference")
        details_json = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        if len(details_json.encode("utf-8")) > MAX_DETAILS_BYTES:
            raise ValueError("details exceed the 20 KB limit")
        clean_importance = _clean_importance(importance)
        expiry = _clean_expiry(expires_at)
        is_temporary = bool(temporary)
        if is_temporary and expiry is None:
            raise ValueError("a temporary memory needs an expires_at timestamp")
        origin_json = json.dumps(_clean_origin(origin), ensure_ascii=False, sort_keys=True)
        record_id = uuid.uuid4().hex
        key = normalise_title(title)
        now = _now()
        competing = self._competing(db, record_type, key, ignore_ids or set())
        db.execute(
            """
            INSERT INTO structured_memory (
                record_id, record_type, title, title_key, body, status, provenance,
                confidence, evidence_json, details_json, supersedes_id,
                created_at, updated_at, resolved_at,
                importance, temporary, expires_at, origin_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id, record_type, title, key, body, status, provenance,
                _clean_confidence(confidence),
                json.dumps(links, ensure_ascii=False), details_json, supersedes_id,
                now, now, now if status == "completed" else None,
                clean_importance, 1 if is_temporary else 0, expiry, origin_json,
            ),
        )
        if status in RETRIEVABLE:
            self._index(db, record_id, title, body)
        AuditLog.write(
            db,
            actor=actor,
            action=audit_action,
            target_type="memory_record",
            target_id=record_id,
            detail={
                "record_type": record_type,
                "provenance": provenance,
                "status": status,
                "conflicts_with": competing,
            },
        )
        return record_id, competing

    @staticmethod
    def _row(db: sqlite3.Connection, record_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM structured_memory WHERE record_id = ?", (record_id,)
        ).fetchone()
        if row is None:
            raise RecordNotFoundError(record_id)
        return row

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["details"] = json.loads(result.pop("details_json"))
        result["origin"] = json.loads(result.pop("origin_json", "{}") or "{}")
        result["temporary"] = bool(result.get("temporary"))
        result.pop("title_key", None)
        return result
