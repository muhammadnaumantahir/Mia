"""Per-reply record of which memories were placed in the prompt.

This is what lets the owner see exactly what Mia used for a particular reply. Rows
hold ids and a short label only. They are removed when the memory, the reply's
conversation or the reply itself is deleted, so the log never outlives its subject.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


class UsageLog:
    def __init__(
        self, connect: Callable[[], AbstractContextManager[sqlite3.Connection]]
    ) -> None:
        self._connect = connect

    def record(
        self,
        *,
        conversation_id: str | None,
        reply_id: str,
        references: Iterable[dict[str, Any]],
    ) -> int:
        """Store the references used for one reply. Idempotent per reply id."""
        rows = [
            (
                conversation_id,
                reply_id,
                str(ref.get("ref_type") or "memory")[:40],
                str(ref.get("ref_id") or "")[:100],
                str(ref.get("title") or "")[:120],
                _now(),
            )
            for ref in references
            if ref.get("ref_id")
        ]
        with self._connect() as db:
            db.execute("DELETE FROM memory_usage WHERE reply_id = ?", (reply_id,))
            db.executemany(
                "INSERT INTO memory_usage "
                "(conversation_id, reply_id, ref_type, ref_id, ref_label, used_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def for_reply(self, reply_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT ref_type, ref_id, ref_label, used_at FROM memory_usage "
                "WHERE reply_id = ? ORDER BY usage_id",
                (reply_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def for_memory(self, ref_type: str, ref_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT conversation_id, reply_id, used_at FROM memory_usage "
                "WHERE ref_type = ? AND ref_id = ? ORDER BY usage_id DESC LIMIT ?",
                (ref_type, ref_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [dict(row) for row in rows]
