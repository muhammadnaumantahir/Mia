"""Append-only SQLite event and metric storage for the Mia observatory."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ObservatoryStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    experiment_id TEXT,
                    day_index INTEGER,
                    layer_id TEXT,
                    category TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS metric_samples (
                    sample_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    experiment_id TEXT,
                    day_index INTEGER NOT NULL CHECK(day_index >= 0),
                    metric_name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    benchmark_id TEXT,
                    source_event_id TEXT,
                    context_json TEXT NOT NULL
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at DESC)"
            )
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_name_day
                ON metric_samples(metric_name, day_index)
            """)

    def record_event(
        self,
        *,
        category: str,
        event_type: str,
        summary: str,
        outcome: str = "recorded",
        day_index: int | None = None,
        experiment_id: str | None = None,
        layer_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> str:
        if day_index is not None and day_index < 0:
            raise ValueError("day_index cannot be negative")
        event_id = uuid.uuid4().hex
        with self._connection() as db:
            db.execute("""
                INSERT INTO events (
                    event_id, occurred_at, experiment_id, day_index, layer_id,
                    category, event_type, outcome, summary, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                utc_now(),
                experiment_id,
                day_index,
                layer_id,
                category,
                event_type,
                outcome,
                summary,
                json.dumps(payload or {}, sort_keys=True),
            ))
        return event_id

    def record_metric(
        self,
        *,
        day_index: int,
        metric_name: str,
        value: float,
        unit: str,
        benchmark_id: str | None = None,
        experiment_id: str | None = None,
        source_event_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        if day_index < 0:
            raise ValueError("day_index cannot be negative")
        sample_id = uuid.uuid4().hex
        with self._connection() as db:
            db.execute("""
                INSERT INTO metric_samples (
                    sample_id, occurred_at, experiment_id, day_index,
                    metric_name, value, unit, benchmark_id, source_event_id,
                    context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sample_id,
                utc_now(),
                experiment_id,
                day_index,
                metric_name,
                float(value),
                unit,
                benchmark_id,
                source_event_id,
                json.dumps(context or {}, sort_keys=True),
            ))
        return sample_id

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        with self._connection() as db:
            rows = db.execute(
                "SELECT * FROM events ORDER BY occurred_at DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_metrics(self, metric_name: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as db:
            if metric_name:
                rows = db.execute("""
                    SELECT * FROM metric_samples
                    WHERE metric_name = ?
                    ORDER BY day_index, occurred_at
                """, (metric_name,)).fetchall()
            else:
                rows = db.execute("""
                    SELECT * FROM metric_samples
                    ORDER BY metric_name, day_index, occurred_at
                """).fetchall()
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        with self._connection() as db:
            event_count = db.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()[0]
            metric_count = db.execute(
                "SELECT COUNT(*) FROM metric_samples"
            ).fetchone()[0]
            latest_day = db.execute(
                "SELECT MAX(day_index) FROM metric_samples"
            ).fetchone()[0]
            latest_event = db.execute(
                "SELECT MAX(occurred_at) FROM events"
            ).fetchone()[0]
        return {
            "event_count": event_count,
            "metric_sample_count": metric_count,
            "latest_day_index": latest_day,
            "latest_event_at": latest_event,
            "data_status": (
                "recorded" if event_count or metric_count else "awaiting_baseline"
            ),
        }
