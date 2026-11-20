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


def _valid_ms(value: float | None) -> float | None:
    if value is None or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _latency_stats(values: list[float]) -> dict[str, Any]:
    """Count, mean, median and nearest-rank p95 of measured values; None when empty."""
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None}
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    middle = len(ordered) // 2
    median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    return {
        "count": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 3),
        "median": round(median, 3),
        "p95": round(ordered[rank - 1], 3),
    }


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
            columns = {row["name"] for row in db.execute("PRAGMA table_info(usage)").fetchall()}
            for column in ("first_token_ms", "total_ms"):
                if column not in columns:
                    db.execute(f"ALTER TABLE usage ADD COLUMN {column} REAL")

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
        first_token_ms: float | None = None,
        total_ms: float | None = None,
    ) -> None:
        # Latency is stored only when it is a finite, non-negative measurement.
        first_token_ms = _valid_ms(first_token_ms)
        total_ms = _valid_ms(total_ms)
        reported = input_tokens is not None and output_tokens is not None
        if cost_usd is not None and (not math.isfinite(cost_usd) or cost_usd < 0):
            cost_usd = None
        with self._connection() as db:
            db.execute(
                """
                INSERT INTO usage (recorded_at, provider_id, model_id, task_type,
                    input_tokens, output_tokens, token_source, cost_usd,
                    free_tier, data_local, streamed, first_token_ms, total_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    first_token_ms,
                    total_ms,
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

    def spend_month_utc(self, now: datetime | None = None) -> float:
        """Total recorded spend in the current UTC calendar month.

        `now` is injectable for deterministic boundary tests; naive values are
        interpreted as UTC rather than as the host's local timezone.
        """
        moment = now or _utc_now()
        moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
        month_start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.spend_since(month_start)

    def window_summary(self, since: str, until: str) -> dict[str, Any]:
        """Totals for recorded_at in [since, until); ISO-8601 UTC strings."""
        with self._connection() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd,
                       COALESCE(SUM(token_source = 'estimated'), 0) AS estimated_rows,
                       COALESCE(SUM(free_tier = 0), 0) AS paid_requests,
                       COALESCE(SUM(data_local = 0), 0) AS non_local_requests
                FROM usage WHERE recorded_at >= ? AND recorded_at < ?
                """,
                (since, until),
            ).fetchone()
            lat = db.execute(
                """
                SELECT first_token_ms, total_ms FROM usage
                WHERE recorded_at >= ? AND recorded_at < ?
                """,
                (since, until),
            ).fetchall()
        return {
            "requests": row["requests"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "cost_usd": round(row["cost_usd"], 8),
            "estimated_token_rows": row["estimated_rows"],
            "paid_requests": row["paid_requests"],
            "non_local_requests": row["non_local_requests"],
            "latency_ms": {
                "total": _latency_stats([r["total_ms"] for r in lat if r["total_ms"] is not None]),
                "first_token": _latency_stats(
                    [r["first_token_ms"] for r in lat if r["first_token_ms"] is not None]
                ),
            },
        }

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
                       MAX(free_tier) AS free_tier, MAX(data_local) AS data_local,
                       AVG(total_ms) AS avg_total_ms, AVG(first_token_ms) AS avg_first_token_ms
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
                "avg_total_ms": None if row["avg_total_ms"] is None else round(row["avg_total_ms"], 1),
                "avg_first_token_ms": (
                    None if row["avg_first_token_ms"] is None
                    else round(row["avg_first_token_ms"], 1)
                ),
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
