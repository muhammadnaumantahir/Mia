"""SQLite conversation history and provenance-aware searchable memory."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_fts_query(query: str) -> str:
    terms = re.findall(r"[\w-]{2,}", query, flags=re.UNICODE)
    return " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])


class MemoryStore:
    MAX_ITEM_BYTES = 1_000_000
    MAX_MESSAGE_CHARS = 100_000

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id)
                        ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_items (
                    item_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source_uri TEXT,
                    source_title TEXT,
                    source_sha256 TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
                    ON conversation_messages(conversation_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_memory_kind_time
                    ON memory_items(kind, created_at DESC);
            """)
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(item_id UNINDEXED, title, content, tags)
            """)

    def ensure_conversation(self, conversation_id: str | None = None) -> str:
        conversation_id = conversation_id or uuid.uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", conversation_id):
            raise ValueError("conversation_id must be 8-80 safe characters")
        now = _now()
        with self._connection() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO conversations (
                    conversation_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (conversation_id, "New conversation", now, now),
            )
        return conversation_id

    def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
    ) -> str:
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if not content.strip() or len(content) > self.MAX_MESSAGE_CHARS:
            raise ValueError("message must be non-empty and at most 100,000 characters")
        self.ensure_conversation(conversation_id)
        message_id = uuid.uuid4().hex
        now = _now()
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO conversation_messages (
                    message_id, conversation_id, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (message_id, conversation_id, role, content, now),
            )
            title = content.strip().splitlines()[0][:80] if role == "user" else None
            if title:
                db.execute(
                    """
                    UPDATE conversations
                    SET title = CASE WHEN title = 'New conversation' THEN ? ELSE title END,
                        updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (title, now, conversation_id),
                )
            else:
                db.execute(
                    "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (now, conversation_id),
                )
        return message_id

    def get_messages(
        self,
        conversation_id: str,
        *,
        limit: int = 12,
    ) -> list[dict[str, str]]:
        safe_limit = max(1, min(int(limit), 50))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT role, content FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (conversation_id, safe_limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                       COUNT(m.message_id) AS message_count
                FROM conversations c
                LEFT JOIN conversation_messages m
                  ON m.conversation_id = c.conversation_id
                GROUP BY c.conversation_id
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connection() as db:
            cursor = db.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    def add_item(
        self,
        *,
        kind: str,
        title: str,
        content: str,
        source_uri: str | None = None,
        source_title: str | None = None,
        trust_level: str = "untrusted",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if kind not in {"fact", "note", "document", "experience", "preference", "research"}:
            raise ValueError("Unsupported memory item kind")
        if trust_level not in {"user", "trusted", "untrusted"}:
            raise ValueError("trust_level must be user, trusted or untrusted")
        if not title.strip() or not content.strip():
            raise ValueError("title and content are required")
        encoded = content.encode("utf-8")
        if len(encoded) > self.MAX_ITEM_BYTES:
            raise ValueError("Memory item exceeds the 1 MB limit")
        clean_tags = [tag.strip()[:50] for tag in (tags or []) if tag.strip()][:30]
        item_id = uuid.uuid4().hex
        now = _now()
        digest = hashlib.sha256(encoded).hexdigest()
        tags_json = json.dumps(clean_tags, ensure_ascii=False)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO memory_items (
                    item_id, kind, title, content, source_uri, source_title,
                    source_sha256, trust_level, tags_json, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    kind,
                    title.strip()[:200],
                    content,
                    source_uri,
                    source_title,
                    digest,
                    trust_level,
                    tags_json,
                    metadata_json,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO memory_fts (item_id, title, content, tags)
                VALUES (?, ?, ?, ?)
                """,
                (item_id, title, content, " ".join(clean_tags)),
            )
        return item_id

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM memory_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
        return self._decode_item(row) if row else None

    def list_items(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT * FROM memory_items
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._decode_item(row) for row in rows]

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        match_query = _safe_fts_query(query)
        if not match_query:
            return []
        safe_limit = max(1, min(int(limit), 100))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT m.*, bm25(memory_fts) AS relevance
                FROM memory_fts
                JOIN memory_items m ON m.item_id = memory_fts.item_id
                WHERE memory_fts MATCH ?
                ORDER BY relevance
                LIMIT ?
                """,
                (match_query, safe_limit),
            ).fetchall()
        return [self._decode_item(row) for row in rows]

    def delete_item(self, item_id: str) -> bool:
        with self._connection() as db:
            row = db.execute(
                "SELECT item_id FROM memory_items WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            if not row:
                return False
            db.execute("DELETE FROM memory_fts WHERE item_id = ?", (item_id,))
            db.execute("DELETE FROM memory_items WHERE item_id = ?", (item_id,))
        return True

    @staticmethod
    def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = json.loads(result.pop("tags_json"))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result
