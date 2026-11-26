"""Versioned schema migrations and the durable memory audit log.

Schema version 1 is the original layout (conversations, messages, knowledge items,
structured records and their FTS indexes), created with ``CREATE TABLE IF NOT
EXISTS`` by ``MemoryStore``. Every later version is an additive migration applied
once, in order, inside a single transaction, and recorded in ``schema_migrations``.
Migrations never drop or rewrite existing memory rows. They are idempotent, so a run
interrupted part-way is safe to repeat (SQLite DDL is not reliably transactional here).
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


def _add_column(db: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """ALTER TABLE ADD COLUMN that is safe to re-run after a partial failure."""
    existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def _migration_3_memory_lifecycle(db: sqlite3.Connection) -> None:
    for column, ddl in (
        ("importance", "REAL NOT NULL DEFAULT 0.5"),
        ("temporary", "INTEGER NOT NULL DEFAULT 0"),
        ("expires_at", "TEXT"),
        ("archived_at", "TEXT"),
        ("origin_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("last_used_at", "TEXT"),
        ("use_count", "INTEGER NOT NULL DEFAULT 0"),
    ):
        _add_column(db, "structured_memory", column, ddl)
    _add_column(db, "conversations", "temporary", "INTEGER NOT NULL DEFAULT 0")
    db.execute("""
        CREATE TABLE IF NOT EXISTS structured_memory_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            change_type TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_structured_history_record
            ON structured_memory_history(record_id, history_id)
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_relationships (
            relationship_id TEXT PRIMARY KEY,
            from_id TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'normal',
            note TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(from_id, to_id, relation)
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_relationships_from ON memory_relationships(from_id)"
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_relationships_to ON memory_relationships(to_id)")
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS memory_usage (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT,
            reply_id TEXT,
            ref_type TEXT NOT NULL,
            ref_id TEXT NOT NULL,
            ref_label TEXT NOT NULL DEFAULT '',
            used_at TEXT NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_usage_reply ON memory_usage(reply_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_usage_ref ON memory_usage(ref_type, ref_id)")


def _migration_4_conversation_project(db: sqlite3.Connection) -> None:
    """Optional project assignment for a conversation (NULL = no project, the default)."""
    _add_column(db, "conversations", "project", "TEXT")


def _migration_5_record_project(db: sqlite3.Connection) -> None:
    """Optional project scope for a structured record (NULL = unscoped, the default)."""
    _add_column(db, "structured_memory", "project", "TEXT")


def _migration_6_personal_items(db: sqlite3.Connection) -> None:
    """Owner-scoped tasks, reminders and notes (MIA-LIFE-01)."""
    from mia.memory.personal import PersonalData

    PersonalData.initialize(db)


def _migration_7_task_manager(db: sqlite3.Connection) -> None:
    """Priority, recurrence, series, duplicate key and structural history (MIA-LIFE-02)."""
    for column, ddl in (
        ("priority", "INTEGER NOT NULL DEFAULT 2"),
        ("recurrence", "TEXT"),
        ("series_id", "TEXT"),
        ("dedupe_key", "TEXT"),
    ):
        _add_column(db, "personal_items", column, ddl)
    db.execute("""
        CREATE TABLE IF NOT EXISTS personal_item_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            field TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_personal_history_item ON personal_item_history(item_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_personal_dedupe ON personal_items(dedupe_key, status)"
    )


def _migration_8_project_workspace(db: sqlite3.Connection) -> None:
    """Project registry (goal, status) and milestones (MIA-LIFE-04)."""
    from mia.memory.projects import ProjectStore

    ProjectStore.initialize(db)


def _migration_9_knowledge_graph(db: sqlite3.Connection) -> None:
    """Typed, evidence-backed edges across records, tasks and project workspaces (MIA-LIFE-10)."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_graph_edges (
            edge_id TEXT PRIMARY KEY,
            from_type TEXT NOT NULL,
            from_id TEXT NOT NULL,
            to_type TEXT NOT NULL,
            to_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            provenance TEXT NOT NULL,
            confidence REAL NOT NULL,
            state TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'normal',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            project_key TEXT,
            note TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT,
            UNIQUE(from_type, from_id, to_type, to_id, relation)
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_graph_project_state "
        "ON knowledge_graph_edges(project_key, state, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_graph_from "
        "ON knowledge_graph_edges(from_type, from_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_graph_to "
        "ON knowledge_graph_edges(to_type, to_id)"
    )
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_graph_edge_history (
            history_id INTEGER PRIMARY KEY AUTOINCREMENT,
            edge_id TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            change_type TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_graph_history_edge "
        "ON knowledge_graph_edge_history(edge_id, history_id)"
    )


# (version, description, function). Append new migrations; never edit applied ones.
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (2, "identity versions and memory audit log", _migration_2_identity_and_audit),
    (3, "memory lifecycle, history, relationships, settings and usage", _migration_3_memory_lifecycle),
    (4, "optional conversation project assignment", _migration_4_conversation_project),
    (5, "optional structured record project scope", _migration_5_record_project),
    (6, "owner-scoped personal items", _migration_6_personal_items),
    (7, "task priority, recurrence, duplicates and history", _migration_7_task_manager),
    (8, "project workspace and milestones", _migration_8_project_workspace),
    (9, "personal knowledge graph with typed evidence-backed edges", _migration_9_knowledge_graph),
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
    # Every migration is idempotent (CREATE ... IF NOT EXISTS / guarded ADD COLUMN), so
    # re-running them is a cheap layout self-check. It repairs a table that was
    # recreated in its original shape after its migration was already recorded.
    for _, _, apply in sorted(MIGRATIONS, key=lambda m: m[0]):
        apply(db)
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
