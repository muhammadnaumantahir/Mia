"""SQLite conversation history and provenance-aware searchable memory."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from mia.memory import schema
from mia.memory.identity import IdentityStore
from mia.memory.relationships import RelationshipStore
from mia.memory.schema import AuditLog
from mia.memory.settings import SettingsStore
from mia.memory.structured import StructuredMemory
from mia.memory.usage import UsageLog


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _safe_fts_query(query: str) -> str:
    terms = re.findall(r"[\w-]{2,}", query, flags=re.UNICODE)
    return " AND ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])


_STOPWORDS = frozenset(
    """
    the and for are but not you your yours with this that these those from have has had was were
    will would could should can cannot did does done what when where which who whom why how about
    into onto over under then than them they their there here just like also very more most some
    any all our ours out off its it's i'm i've i'll don't doesn't didn't isn't aren't wasn't
    please tell give let know want need get got make made use using been being been once again
    mia hey hello thanks thank
    """.split()
)


def extract_query_terms(text: str, *, max_terms: int = 10) -> list[str]:
    """Distinctive lowercase search terms from free text.

    Drops very short words and common stopwords, removes duplicates, and keeps
    the longest (usually most specific) terms while preserving first-seen order.
    """
    seen: list[str] = []
    for term in re.findall(r"[\w-]{3,}", text.lower(), flags=re.UNICODE):
        if term in _STOPWORDS or term in seen:
            continue
        seen.append(term)
    keep = set(sorted(seen, key=lambda t: (-len(t), seen.index(t)))[: max(1, max_terms)])
    return [term for term in seen if term in keep]


def _retrieval_fts_query(text: str) -> str:
    """OR-query over distinctive terms, ranked by bm25 (unlike the strict AND search)."""
    return " OR ".join(f'"{term.replace(chr(34), "")}"' for term in extract_query_terms(text))


class MemoryStore:
    MAX_ITEM_BYTES = 1_000_000
    MAX_EXPORT_BYTES = 25 * 1024 * 1024
    MAX_MESSAGE_CHARS = 100_000

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.structured = StructuredMemory(self._connection)
        self.audit = AuditLog(self._connection)
        self.identity = IdentityStore(self._connection)
        self.settings = SettingsStore(self._connection)
        self.relationships = RelationshipStore(self._connection)
        self.usage = UsageLog(self._connection)
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

                CREATE TABLE IF NOT EXISTS memory_embedding_cache (
                    model_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    embedding_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(model_id, content_sha256)
                );
            """)
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
                USING fts5(item_id UNINDEXED, title, content, tags)
            """)
            StructuredMemory.initialize(db)
            # Full-text index over conversation messages so earlier turns and other
            # conversations can be retrieved. Its rowid mirrors the message rowid and
            # triggers keep it in sync, including rows removed by ON DELETE CASCADE.
            db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS message_fts
                USING fts5(message_id UNINDEXED, conversation_id UNINDEXED, content)
            """)
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS trg_message_fts_insert
                AFTER INSERT ON conversation_messages BEGIN
                    INSERT INTO message_fts (rowid, message_id, conversation_id, content)
                    VALUES (new.rowid, new.message_id, new.conversation_id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS trg_message_fts_delete
                AFTER DELETE ON conversation_messages BEGIN
                    DELETE FROM message_fts WHERE rowid = old.rowid;
                END;
            """)
            indexed = db.execute("SELECT COUNT(*) FROM message_fts").fetchone()[0]
            stored = db.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0]
            if indexed != stored:  # databases created before the index existed
                db.execute("DELETE FROM message_fts")
                db.execute("""
                    INSERT INTO message_fts (rowid, message_id, conversation_id, content)
                    SELECT rowid, message_id, conversation_id, content
                    FROM conversation_messages
                """)
            # Additive, versioned migrations; existing memories are never rewritten.
            schema.migrate(db)
            IdentityStore.seed(db)

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

    def recent_messages(
        self, *, limit: int = 20, exclude_message_ids: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        """Recent messages across non-temporary conversations for local semantic retrieval."""
        safe_limit = max(1, min(int(limit), 100))
        excluded = list({str(value) for value in exclude_message_ids if value})
        clause = ""
        params: list[Any] = []
        if excluded:
            clause = " AND m.message_id NOT IN (" + ",".join("?" for _ in excluded) + ")"
            params.extend(excluded)
        params.append(safe_limit)
        with self._connection() as db:
            rows = db.execute(
                "SELECT m.message_id,m.conversation_id,m.role,m.content,m.created_at,"
                "c.title AS conversation_title FROM conversation_messages m "
                "JOIN conversations c ON c.conversation_id=m.conversation_id "
                "WHERE c.temporary=0" + clause +
                " ORDER BY m.created_at DESC,m.rowid DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_message_ids(self, conversation_id: str, limit: int = 12) -> list[str]:
        """Ids of the newest messages, matching the window `get_messages` returns."""
        safe_limit = max(1, min(int(limit), 50))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT message_id FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (conversation_id, safe_limit),
            ).fetchall()
        return [str(row["message_id"]) for row in rows]

    def retrieve_items(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Knowledge items matching ANY distinctive term in free text, best first."""
        match_query = _retrieval_fts_query(query)
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

    def retrieve_records(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Structured records (decisions, goals, ...) matching ANY distinctive term."""
        return self.structured.retrieve(_retrieval_fts_query(query), limit)

    def retrieve_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        exclude_message_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        """Past messages (any conversation) matching distinctive terms, best first."""
        match_query = _retrieval_fts_query(query)
        if not match_query:
            return []
        excluded = set(exclude_message_ids)
        safe_limit = max(1, min(int(limit), 100))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT m.message_id, m.conversation_id, m.role, m.content, m.created_at,
                       c.title AS conversation_title, bm25(message_fts) AS relevance
                FROM message_fts
                JOIN conversation_messages m ON m.rowid = message_fts.rowid
                JOIN conversations c ON c.conversation_id = m.conversation_id
                WHERE message_fts MATCH ? AND c.temporary = 0
                ORDER BY relevance
                LIMIT ?
                """,
                (match_query, safe_limit + len(excluded)),
            ).fetchall()
        results = [dict(row) for row in rows if row["message_id"] not in excluded]
        return results[:safe_limit]

    def list_conversations(self, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT c.conversation_id, c.title, c.created_at, c.updated_at, c.temporary,
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

    def export_data(self) -> dict[str, Any]:
        """Return a bounded, checksummed export of conversations and knowledge."""
        with self._connection() as db:
            conversations = db.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM conversations
                ORDER BY created_at, conversation_id
                """
            ).fetchall()
            messages = db.execute(
                """
                SELECT conversation_id, role, content, created_at
                FROM conversation_messages
                ORDER BY conversation_id, created_at, rowid
                """
            ).fetchall()
            items = db.execute(
                "SELECT * FROM memory_items ORDER BY created_at, item_id"
            ).fetchall()

        messages_by_conversation: dict[str, list[dict[str, str]]] = {}
        for row in messages:
            messages_by_conversation.setdefault(
                str(row["conversation_id"]), []
            ).append({
                "role": str(row["role"]),
                "content": str(row["content"]),
                "created_at": str(row["created_at"]),
            })

        payload: dict[str, Any] = {
            "schema_version": 1,
            "exported_at": _now(),
            "conversations": [
                {
                    **dict(row),
                    "messages": messages_by_conversation.get(
                        str(row["conversation_id"]), []
                    ),
                }
                for row in conversations
            ],
            "memory_items": [self._decode_item(row) for row in items],
            "structured_memory": self.structured.export(),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        digest_field = json.dumps({"sha256": digest}, separators=(",", ":")).encode("utf-8")
        if len(canonical) + len(digest_field) > self.MAX_EXPORT_BYTES:
            raise ValueError("Memory export exceeds the 25 MB safety limit")
        payload["sha256"] = digest
        return payload

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT conversation_id, title, created_at, updated_at, temporary
                FROM conversations
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._connection() as db:
            db.execute(
                "DELETE FROM memory_usage WHERE conversation_id = ?", (conversation_id,)
            )
            cursor = db.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )
        return cursor.rowcount > 0

    def purge_messages(self, message_ids: list[str], *, actor: str = "owner") -> int:
        """Delete specific chat messages (and their search-index rows). Returns the count."""
        removed = 0
        with self._connection() as db:
            for message_id in message_ids:
                cursor = db.execute(
                    "DELETE FROM conversation_messages WHERE message_id = ?", (message_id,)
                )
                removed += cursor.rowcount
            if removed:
                AuditLog.write(
                    db,
                    actor=actor,
                    action="messages_purged",
                    target_type="conversation_message",
                    target_id=",".join(message_ids)[:200],
                    detail={"count": removed, "reason": "source of a deleted memory"},
                )
        return removed

    def forget_record(
        self, record_id: str, *, purge_source: bool = True, chain: bool = True,
        actor: str = "owner",
    ) -> dict[str, Any]:
        """Delete a memory and, by default, the chat message(s) it was captured from.

        Without purging the source message, the deleted fact would stay searchable
        through conversation history, so purging is the default.
        """
        sources = self.structured.source_message_ids(record_id, chain=chain) if purge_source else []
        deleted = self.structured.delete(record_id, chain=chain, actor=actor)
        purged = self.purge_messages(sources, actor=actor) if deleted and sources else 0
        return {"deleted": deleted, "purged_messages": purged}

    def set_conversation_temporary(self, conversation_id: str, temporary: bool) -> bool:
        """Temporary conversations capture no memory and are never retrieved elsewhere."""
        with self._connection() as db:
            cursor = db.execute(
                "UPDATE conversations SET temporary = ? WHERE conversation_id = ?",
                (1 if temporary else 0, conversation_id),
            )
            if cursor.rowcount:
                AuditLog.write(
                    db,
                    actor="owner",
                    action="conversation_temporary_set",
                    target_type="conversation",
                    target_id=conversation_id,
                    detail={"temporary": bool(temporary)},
                )
        return cursor.rowcount > 0

    def is_conversation_temporary(self, conversation_id: str) -> bool:
        with self._connection() as db:
            row = db.execute(
                "SELECT temporary FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return bool(row and row["temporary"])

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

    def get_embedding_cache(self, model_id: str, content_sha256: str) -> list[float] | None:
        """Read a local content-addressed embedding; corrupt rows are treated as cache misses."""
        with self._connection() as db:
            row = db.execute(
                "SELECT embedding_json FROM memory_embedding_cache "
                "WHERE model_id=? AND content_sha256=?",
                (model_id, content_sha256),
            ).fetchone()
        if row is None:
            return None
        try:
            vector = json.loads(row["embedding_json"])
            if (not isinstance(vector, list) or not vector
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               and math.isfinite(v) for v in vector)):
                return None
            return [float(v) for v in vector]
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def set_embedding_cache(
        self, model_id: str, content_sha256: str, vector: list[float], *, max_entries: int = 5000
    ) -> None:
        """Store only a validated embedding vector, never the source memory text."""
        if (not isinstance(model_id, str) or not model_id or len(model_id) > 120
                or not re.fullmatch(r"[a-f0-9]{64}", content_sha256)
                or not isinstance(vector, list) or not vector or len(vector) > 4096
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in vector)):
            raise ValueError("invalid embedding cache entry")
        safe_limit = max(100, min(int(max_entries), 20000))
        with self._connection() as db:
            db.execute(
                "INSERT INTO memory_embedding_cache(model_id,content_sha256,embedding_json,created_at) "
                "VALUES(?,?,?,?) ON CONFLICT(model_id,content_sha256) DO UPDATE SET "
                "embedding_json=excluded.embedding_json, created_at=excluded.created_at",
                (model_id, content_sha256, json.dumps([float(v) for v in vector]), _now()),
            )
            db.execute(
                "DELETE FROM memory_embedding_cache WHERE rowid IN ("
                "SELECT rowid FROM memory_embedding_cache ORDER BY created_at DESC "
                "LIMIT -1 OFFSET ?)",
                (safe_limit,),
            )

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
            db.execute(
                "DELETE FROM memory_usage WHERE ref_type = 'knowledge' AND ref_id = ?",
                (item_id,),
            )
            AuditLog.write(
                db,
                actor="owner",
                action="item_deleted",
                target_type="knowledge_item",
                target_id=item_id,
                detail={},
            )
        return True

    def purge_older_than(
        self,
        days: int,
        *,
        conversations: bool = True,
        items: bool = True,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Owner-triggered retention. Selects by last update older than `days`.

        Defaults to a dry run that deletes nothing. Real deletion is one
        transaction covering messages, items and their search index rows.
        """
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise ValueError("days must be an integer of at least 1")
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        result: dict[str, Any] = {
            "cutoff": cutoff,
            "dry_run": dry_run,
            "conversations": {"count": 0, "sample": []},
            "items": {"count": 0, "sample": []},
        }
        with self._connection() as db:
            if conversations:
                rows = db.execute(
                    "SELECT conversation_id, title FROM conversations WHERE updated_at < ?"
                    " ORDER BY updated_at",
                    (cutoff,),
                ).fetchall()
                result["conversations"] = {
                    "count": len(rows),
                    "sample": [
                        {"id": r["conversation_id"], "title": r["title"]} for r in rows[:20]
                    ],
                }
                if not dry_run:
                    for row in rows:
                        db.execute(
                            "DELETE FROM conversation_messages WHERE conversation_id = ?",
                            (row["conversation_id"],),
                        )
                        db.execute(
                            "DELETE FROM conversations WHERE conversation_id = ?",
                            (row["conversation_id"],),
                        )
            if items:
                rows = db.execute(
                    "SELECT item_id, title FROM memory_items WHERE updated_at < ?"
                    " ORDER BY updated_at",
                    (cutoff,),
                ).fetchall()
                result["items"] = {
                    "count": len(rows),
                    "sample": [{"id": r["item_id"], "title": r["title"]} for r in rows[:20]],
                }
                if not dry_run:
                    for row in rows:
                        db.execute("DELETE FROM memory_fts WHERE item_id = ?", (row["item_id"],))
                        db.execute("DELETE FROM memory_items WHERE item_id = ?", (row["item_id"],))
        return result

    @staticmethod
    def _decode_item(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = json.loads(result.pop("tags_json"))
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result
