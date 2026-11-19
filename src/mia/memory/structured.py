"""Structured, provenance-aware memory: decisions, goals, episodes, projects, entities.

Unlike free-form knowledge items these records have a lifecycle. Owner corrections
never overwrite in place: a correction creates a new record that supersedes the
old one, and the old record is kept (with a link to its successor) for audit.
Competing active records with the same title are surfaced, not silently merged.
Model-generated speculation can only be stored as ``inferred`` or ``hypothesis``,
and inferred/validated/source-backed records must cite their evidence.
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

RECORD_TYPES = frozenset({"decision", "goal", "episode", "project", "entity"})
STATUSES = frozenset({"active", "completed", "superseded", "rejected"})
PROVENANCE = frozenset(
    {"owner_provided", "observed", "source_backed", "inferred", "hypothesis", "validated"}
)
EVIDENCE_REQUIRED = frozenset({"inferred", "validated", "source_backed"})
EVIDENCE_TYPES = frozenset({"message", "knowledge", "record", "url", "tool_event", "other"})

MAX_TITLE = 200
MAX_BODY = 20_000
MAX_EVIDENCE = 20
MAX_DETAILS_BYTES = 20_000


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
    ) -> tuple[str, list[str]]:
        """Owner correction: create a successor and retire the old record, atomically."""
        if title is None and body is None and details is None:
            raise ValueError("a correction must change the title, body or details")
        with self._connect() as db:
            old = self._row(db, record_id)
            if old["status"] not in {"active", "completed"}:
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
        return new_id, conflicts

    def set_status(self, record_id: str, status: str) -> None:
        """active <-> completed, or reject an active/completed record (final)."""
        if status not in {"active", "completed", "rejected"}:
            raise ValueError("status must be active, completed or rejected")
        with self._connect() as db:
            row = self._row(db, record_id)
            if row["status"] in {"superseded", "rejected"}:
                raise RecordStateError(f"record is {row['status']} and cannot change status")
            now = _now()
            db.execute(
                "UPDATE structured_memory SET status = ?, updated_at = ?, resolved_at = ? "
                "WHERE record_id = ?",
                (status, now, None if status == "active" else now, record_id),
            )
            if status == "rejected":
                db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))

    def delete(self, record_id: str) -> bool:
        with self._connect() as db:
            if not db.execute(
                "SELECT 1 FROM structured_memory WHERE record_id = ?", (record_id,)
            ).fetchone():
                return False
            db.execute("DELETE FROM structured_fts WHERE record_id = ?", (record_id,))
            db.execute("DELETE FROM structured_memory WHERE record_id = ?", (record_id,))
        return True

    # ------------------------------------------------------------------- read

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM structured_memory WHERE record_id = ?", (record_id,)
            ).fetchone()
        return self._decode(row) if row else None

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
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if record_type:
            clauses.append("record_type = ?")
            params.append(record_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM structured_memory {where} "  # noqa: S608 - fixed clauses only
                "ORDER BY updated_at DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def retrieve(self, match_query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Active/completed records matching a prepared FTS query, best first."""
        if not match_query:
            return []
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT s.*, bm25(structured_fts) AS relevance
                FROM structured_fts
                JOIN structured_memory s ON s.record_id = structured_fts.record_id
                WHERE structured_fts MATCH ? AND s.status IN ('active', 'completed')
                ORDER BY relevance
                LIMIT ?
                """,
                (match_query, max(1, min(int(limit), 100))),
            ).fetchall()
        return [self._decode(row) for row in rows]

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
    ) -> tuple[str, list[str]]:
        if record_type not in RECORD_TYPES:
            raise ValueError("record_type must be one of " + ", ".join(sorted(RECORD_TYPES)))
        if provenance not in PROVENANCE:
            raise ValueError("provenance must be one of " + ", ".join(sorted(PROVENANCE)))
        if status not in {"active", "completed"}:
            raise ValueError("new records must be active or completed")
        title = title.strip()
        body = body.strip()
        if not title or not body:
            raise ValueError("title and body are required")
        if len(title) > MAX_TITLE or len(body) > MAX_BODY:
            raise ValueError(f"title is limited to {MAX_TITLE} and body to {MAX_BODY} characters")
        links = _clean_evidence(evidence)
        if provenance in EVIDENCE_REQUIRED and not links:
            raise ValueError(f"{provenance} records must cite at least one evidence reference")
        details_json = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        if len(details_json.encode("utf-8")) > MAX_DETAILS_BYTES:
            raise ValueError("details exceed the 20 KB limit")
        record_id = uuid.uuid4().hex
        key = normalise_title(title)
        now = _now()
        competing = [
            row["record_id"]
            for row in db.execute(
                "SELECT record_id FROM structured_memory "
                "WHERE record_type = ? AND title_key = ? AND status = 'active'",
                (record_type, key),
            ).fetchall()
            if row["record_id"] not in (ignore_ids or set())
        ]
        db.execute(
            """
            INSERT INTO structured_memory (
                record_id, record_type, title, title_key, body, status, provenance,
                confidence, evidence_json, details_json, supersedes_id,
                created_at, updated_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id, record_type, title, key, body, status, provenance,
                _clean_confidence(confidence),
                json.dumps(links, ensure_ascii=False), details_json, supersedes_id,
                now, now, now if status == "completed" else None,
            ),
        )
        db.execute(
            "INSERT INTO structured_fts (record_id, title, body) VALUES (?, ?, ?)",
            (record_id, title, body),
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
        result.pop("title_key", None)
        return result
