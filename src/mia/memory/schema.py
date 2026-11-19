"""Versioned schema migrations and the durable memory audit log.

Schema version 1 is the original layout (conversations, messages, knowledge items,
structured records and their FTS indexes), created with ``CREATE TABLE IF NOT
EXISTS`` by ``MemoryStore``. Every later version is an additive migration applied
once, in order, inside a single transaction, and recorded in ``schema_migrations``.
Migrations never drop or rewrite existing memory rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

BASELINE_VERSION = 1


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _migration_2_identity_and_audit(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS identity_versions (
            version INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_json TEXT NOT NULL,
            profile_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            reason TEXT NOT NULL,
            parent_version INTEGER
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_audit_log (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_target
            ON memory_audit_log(target_type, target_id, audit_id)
    """)


# (version, description, function). Append new migrations; never edit applied ones.
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (2, "identity versions and memory audit log", _migration_2_identity_and_audit),
]

LATEST_VERSION = max([BASELINE_VERSION, *[version for version, _, _ in MIGRATIONS]])


def current_version(db: sqlite3.Connection) -> int:
    row = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def migrate(db: sqlite3.Connection) -> list[int]:
    """Bring the database to LATEST_VERSION. Returns the versions applied now."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    have = current_version(db)
    if have == 0:
        # Baseline tables were just created (new database) or already existed (upgrade).
        db.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
            (BASELINE_VERSION, "baseline conversations, knowledge and structured records", _now()),
        )
        have = BASELINE_VERSION
    if have > LATEST_VERSION:
        raise RuntimeError(
            f"Memory database schema {have} is newer than this Mia build ({LATEST_VERSION}); "
            "refusing to open it so no memory is damaged"
        )
    applied: list[int] = []
    for version, description, apply in sorted(MIGRATIONS, key=lambda m: m[0]):
        if version <= have:
            continue
        apply(db)
        db.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
            (version, description, _now()),
        )
        applied.append(version)
    return applied


class AuditLog:
    """Append-only record of important memory and identity changes.

    Entries hold ids, field names and counts. Memory text is never copied into the
    log, so deleting a memory also removes its content from everything Mia keeps.
    """

    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    @staticmethod
    def write(
        db: sqlite3.Connection,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Write inside the caller's transaction so state and audit commit together."""
        db.execute(
            """
            INSERT INTO memory_audit_log
                (created_at, actor, action, target_type, target_id, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _now(),
                actor,
                action,
                target_type,
                str(target_id),
                json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            ),
        )

    def record(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as db:
            self.write(
                db,
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                detail=detail,
            )

    def list(
        self,
        *,
        target_type: str | None = None,
        target_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if target_type:
            clauses.append("target_type = ?")
            params.append(target_type)
        if target_id:
            clauses.append("target_id = ?")
            params.append(target_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as db:
            rows: Iterable[sqlite3.Row] = db.execute(
                f"SELECT * FROM memory_audit_log {where} ORDER BY audit_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [
            {
                "audit_id": row["audit_id"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "action": row["action"],
                "target_type": row["target_type"],
                "target_id": row["target_id"],
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]
