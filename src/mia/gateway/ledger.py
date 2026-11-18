"""Persistent, append-only usage and cost ledger for model calls.

Every completion records real provider-reported token counts when available
(otherwise a labelled estimate) and its cost. Spend totals feed runtime budget
enforcement so caps are applied to actual recorded spend, not documentation.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(UTC)


class UsageLedger:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    token_source TEXT NOT NULL CHECK (token_source IN ('reported','estimated')),
                    cost_usd REAL,
                    free_tier INTEGER NOT NULL,
                    data_local INTEGER NOT NULL,
                    streamed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS usage_time ON usage(recorded_at)")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record(
        self,
        *,
        provider_id: str,
        model_id: str,
        task_type: str,
        input_tokens: int | None,
        output_tokens: int | None,
        estimated_input_tokens: int,
        estimated_output_tokens: int,
        cost_usd: float | None,
        free_tier: bool,
        data_local: bool,
        streamed: bool = False,
    ) -> None:
        reported = input_tokens is not None and output_tokens is not None
        if cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0):
            cost_usd = None
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO usage (recorded_at, provider_id, model_id, task_type,
                    input_tokens, output_tokens, token_source, cost_usd,
                    free_tier, data_local, streamed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _utc_now().isoformat(),
                    provider_id,
                    model_id,
                    task_type,
                    max(0, input_tokens if reported else estimated_input_tokens),
                    max(0, output_tokens if reported else estimated_output_tokens),
                    "reported" if reported else "estimated",
                    cost_usd,
                    int(free_tier),
                    int(data_local),
                    int(streamed),
                ),
            )

    def spend_since(self, since: datetime) -> float:
        """Total recorded USD since `since`. Unknown-cost rows add nothing but are
        impossible on paid routes, which require valid prices to be selected."""
        with self._connection() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage WHERE recorded_at >= ?",
                (since.isoformat(),),
            ).fetchone()
        return float(row["total"])

    def spend_today_utc(self) -> float:
        now = _utc_now()
        return self.spend_since(now.replace(hour=0, minute=0, second=0, microsecond=0))

    def summary(self, days: int = 30) -> dict[str, Any]:
        days = max(1, min(int(days), 365))
        since = (_utc_now() - timedelta(days=days)).isoformat()
        with self._connection() as db:
            rows = db.execute(
                """
                SELECT provider_id, model_id, COUNT(*) AS requests,
                       SUM(input_tokens) AS input_tokens,
                       SUM(output_tokens) AS output_tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       SUM(token_source = 'estimated') AS estimated_rows,
                       MAX(free_tier) AS free_tier, MAX(data_local) AS data_local
                FROM usage WHERE recorded_at >= ?
                GROUP BY provider_id, model_id
                ORDER BY cost_usd DESC, requests DESC
                """,
                (since,),
            ).fetchall()
        items = [
            {
                "provider_id": row["provider_id"],
                "model_id": row["model_id"],
                "requests": row["requests"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cost_usd": round(row["cost_usd"], 8),
                "estimated_token_rows": row["estimated_rows"],
                "free_tier": bool(row["free_tier"]),
                "data_local": bool(row["data_local"]),
            }
            for row in rows
        ]
        return {
            "window_days": days,
            "total_requests": sum(item["requests"] for item in items),
            "total_input_tokens": sum(item["input_tokens"] for item in items),
            "total_output_tokens": sum(item["output_tokens"] for item in items),
            "total_cost_usd": round(sum(item["cost_usd"] for item in items), 8),
            "spent_today_utc_usd": round(self.spend_today_utc(), 8),
            "items": items,
        }
