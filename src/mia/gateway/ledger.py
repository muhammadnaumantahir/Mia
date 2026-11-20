"""Persistent, append-only usage and cost ledger for model calls.

Every completion records real provider-reported token counts when available
(otherwise a labelled estimate) and its cost. Spend totals feed runtime budget
enforcement so caps are applied to actual recorded spend, not documentation.
"""

from __future__ import annotations

import math
import sqlite3
import uuid
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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS budget_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    reserved_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    reserved_usd REAL NOT NULL CHECK (reserved_usd >= 0)
                )
                """
            )
            db.execute("CREATE INDEX IF NOT EXISTS reservations_time ON budget_reservations(reserved_at, expires_at)")
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
        reservation_id: str | None = None,
    ) -> None:
        # Latency is stored only when it is a finite, non-negative measurement.
        first_token_ms = _valid_ms(first_token_ms)
        total_ms = _valid_ms(total_ms)
        reported = input_tokens is not None and output_tokens is not None
        if cost_usd is not None and (
            isinstance(cost_usd, bool) or not isinstance(cost_usd, (int, float))
            or not math.isfinite(cost_usd) or cost_usd < 0
        ):
            cost_usd = None
        if not free_tier and cost_usd is None:
            # Keep any live reservation intact. Unknown paid spend is never
            # written as zero or allowed to release the reserved budget.
            raise ValueError("Paid inference usage has unknown cost; reservation retained")
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
            if reservation_id:
                db.execute(
                    "DELETE FROM budget_reservations WHERE reservation_id = ?",
                    (reservation_id,),
                )

    def spend_since(self, since: datetime) -> float | None:
        """Total paid USD since `since`; unknown paid rows fail closed."""
        with self._connection() as db:
            unknown = db.execute(
                "SELECT COUNT(*) AS count FROM usage "
                "WHERE recorded_at >= ? AND free_tier=0 AND cost_usd IS NULL",
                (since.isoformat(),),
            ).fetchone()
            if int(unknown["count"]) > 0:
                return None
            row = db.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage "
                "WHERE recorded_at >= ? AND free_tier=0",
                (since.isoformat(),),
            ).fetchone()
        return float(row["total"])

    def spend_today_utc(self) -> float | None:
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


    def reserve_budget(
        self,
        *,
        provider_id: str,
        model_id: str,
        reserved_usd: float,
        per_request_limit_usd: float,
        daily_limit_usd: float,
        monthly_limit_usd: float,
        now: datetime | None = None,
        ttl_seconds: int = 21_600,
    ) -> str | None:
        """Atomically reserve a paid request against actual spend and other live reservations.

        Reservations share the same SQLite database and write transaction as usage
        rows. The caller releases on failure or passes the ID to record() so the
        actual usage row and reservation removal commit together.
        """
        values = (reserved_usd, per_request_limit_usd, daily_limit_usd, monthly_limit_usd)
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0
            for value in values
        ):
            return None
        if ttl_seconds < 60 or ttl_seconds > 86_400 or reserved_usd <= 0:
            return None
        if reserved_usd > per_request_limit_usd:
            return None

        moment = now or _utc_now()
        moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
        today_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        now_text = moment.isoformat()
        reservation_id = uuid.uuid4().hex
        expires_at = (moment + timedelta(seconds=ttl_seconds)).isoformat()

        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM budget_reservations WHERE expires_at <= ?", (now_text,))
                unknown_daily = db.execute(
                    "SELECT COUNT(*) FROM usage WHERE recorded_at >= ? "
                    "AND free_tier=0 AND cost_usd IS NULL",
                    (today_start.isoformat(),),
                ).fetchone()[0]
                unknown_month = db.execute(
                    "SELECT COUNT(*) FROM usage WHERE recorded_at >= ? "
                    "AND free_tier=0 AND cost_usd IS NULL",
                    (month_start.isoformat(),),
                ).fetchone()[0]
                if int(unknown_daily) or int(unknown_month):
                    return None
                actual_daily = db.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage "
                    "WHERE recorded_at >= ? AND free_tier=0",
                    (today_start.isoformat(),),
                ).fetchone()[0]
                actual_month = db.execute(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage "
                    "WHERE recorded_at >= ? AND free_tier=0",
                    (month_start.isoformat(),),
                ).fetchone()[0]
                reserved_daily = db.execute(
                    "SELECT COALESCE(SUM(reserved_usd), 0) FROM budget_reservations "
                    "WHERE expires_at > ?",
                    (now_text,),
                ).fetchone()[0]
                reserved_month = db.execute(
                    "SELECT COALESCE(SUM(reserved_usd), 0) FROM budget_reservations "
                    "WHERE expires_at > ?",
                    (now_text,),
                ).fetchone()[0]
                epsilon = 1e-12
                if float(actual_daily) + float(reserved_daily) + reserved_usd > daily_limit_usd + epsilon:
                    return None
                if float(actual_month) + float(reserved_month) + reserved_usd > monthly_limit_usd + epsilon:
                    return None
                db.execute(
                    "INSERT INTO budget_reservations "
                    "(reservation_id,reserved_at,expires_at,provider_id,model_id,reserved_usd) "
                    "VALUES (?,?,?,?,?,?)",
                    (reservation_id, now_text, expires_at, provider_id, model_id, reserved_usd),
                )
        except sqlite3.Error:
            return None
        return reservation_id

    def release_reservation(self, reservation_id: str | None) -> bool:
        if not reservation_id:
            return False
        with self._connection() as db:
            changed = db.execute(
                "DELETE FROM budget_reservations WHERE reservation_id = ?",
                (reservation_id,),
            ).rowcount
        return changed > 0

    def clear_reservations_on_startup(self) -> int:
        """Remove leases left behind by a previous single-server process."""
        with self._connection() as db:
            return db.execute("DELETE FROM budget_reservations").rowcount

    def reservation_summary(self) -> dict[str, Any]:
        moment = _utc_now()
        with self._connection() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count, COALESCE(SUM(reserved_usd),0) AS reserved "
                "FROM budget_reservations WHERE expires_at > ?",
                (moment.isoformat(),),
            ).fetchone()
        active_reserved = float(row["reserved"])
        # An in-flight call can cross a UTC day/month boundary. Count every
        # unexpired reservation against the current window until usage is posted.
        return {
            "active_count": int(row["count"]),
            "active_reserved_usd": active_reserved,
            "reserved_today_utc_usd": active_reserved,
            "reserved_month_utc_usd": active_reserved,
        }

    def window_summary(self, since: str, until: str) -> dict[str, Any]:
        """Totals for recorded_at in [since, until); ISO-8601 UTC strings."""
        with self._connection() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(input_tokens), 0) AS input_tokens,
                       COALESCE(SUM(output_tokens), 0) AS output_tokens,
                       COALESCE(SUM(CASE WHEN free_tier=0 THEN cost_usd ELSE 0 END), 0) AS cost_usd,
                       COALESCE(SUM(CASE WHEN free_tier=0 AND cost_usd IS NULL THEN 1 ELSE 0 END), 0) AS unknown_cost_rows,
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
            "cost_usd": None if row["unknown_cost_rows"] else round(row["cost_usd"], 8),
            "unknown_cost_rows": row["unknown_cost_rows"],
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
                       COALESCE(SUM(CASE WHEN free_tier=0 THEN cost_usd ELSE 0 END), 0) AS cost_usd,
                       SUM(CASE WHEN free_tier=0 AND cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_cost_rows,
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
                "cost_usd": None if row["unknown_cost_rows"] else round(row["cost_usd"], 8),
                "unknown_cost_rows": row["unknown_cost_rows"],
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
        spent_today = self.spend_today_utc()
        return {
            "window_days": days,
            "total_requests": sum(item["requests"] for item in items),
            "total_input_tokens": sum(item["input_tokens"] for item in items),
            "total_output_tokens": sum(item["output_tokens"] for item in items),
            "total_cost_usd": (
                None if any(item["unknown_cost_rows"] for item in items)
                else round(sum(item["cost_usd"] or 0.0 for item in items), 8)
            ),
            "spent_today_utc_usd": None if spent_today is None else round(spent_today, 8),
            "items": items,
        }
