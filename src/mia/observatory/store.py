"""Append-only SQLite event, metric and experiment storage."""

from __future__ import annotations

import hashlib
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


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
            db.executescript("""
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
                );

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
                );

                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    planned_days INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS experiment_baselines (
                    experiment_id TEXT PRIMARY KEY REFERENCES experiments(experiment_id),
                    captured_at TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                    day_index INTEGER NOT NULL CHECK(day_index >= 0),
                    captured_at TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    UNIQUE(experiment_id, day_index)
                );

                CREATE INDEX IF NOT EXISTS idx_events_time
                    ON events(occurred_at DESC);
                CREATE INDEX IF NOT EXISTS idx_metrics_name_day
                    ON metric_samples(metric_name, day_index);
                CREATE INDEX IF NOT EXISTS idx_snapshots_experiment_day
                    ON daily_snapshots(experiment_id, day_index);

                CREATE TRIGGER IF NOT EXISTS immutable_baseline_update
                BEFORE UPDATE ON experiment_baselines
                BEGIN
                    SELECT RAISE(ABORT, 'experiment baseline is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS immutable_baseline_delete
                BEFORE DELETE ON experiment_baselines
                BEGIN
                    SELECT RAISE(ABORT, 'experiment baseline is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS immutable_snapshot_update
                BEFORE UPDATE ON daily_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'daily snapshot is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS immutable_snapshot_delete
                BEFORE DELETE ON daily_snapshots
                BEGIN
                    SELECT RAISE(ABORT, 'daily snapshot is immutable');
                END;
            """)

    def prepare_experiment(
        self,
        *,
        name: str,
        baseline_manifest: dict[str, Any],
        planned_days: int = 30,
    ) -> dict[str, str]:
        """Create a prepared experiment with an immutable Day 0 baseline.

        The caller must supply evidence from the real machine and fixed tests.
        This method does not start autonomous execution.
        """
        if not 1 <= planned_days <= 365:
            raise ValueError("planned_days must be between 1 and 365")
        required = {
            "source_commit",
            "configuration_hash",
            "benchmark_suite_id",
            "environment",
            "baseline_results",
            "verification_passed",
        }
        missing = sorted(required.difference(baseline_manifest))
        if missing:
            raise ValueError(f"baseline manifest is missing: {', '.join(missing)}")
        if baseline_manifest["verification_passed"] is not True:
            raise ValueError("baseline verification must pass before preparation")
        source_commit = baseline_manifest["source_commit"]
        if (
            not isinstance(source_commit, str)
            or len(source_commit) not in {40, 64}
            or any(char not in "0123456789abcdef" for char in source_commit)
        ):
            raise ValueError("source_commit must be a full lowercase Git commit hash")
        configuration_hash = baseline_manifest["configuration_hash"]
        if (
            not isinstance(configuration_hash, str)
            or not configuration_hash.startswith("sha256:")
            or len(configuration_hash) != 71
            or any(char not in "0123456789abcdef" for char in configuration_hash[7:])
        ):
            raise ValueError("configuration_hash must be a SHA-256 digest")
        if baseline_manifest["benchmark_suite_id"] != "mia-foundation-v2":
            raise ValueError("baseline must use the fixed mia-foundation-v2 suite")
        environment = baseline_manifest["environment"]
        if not isinstance(environment, dict) or not all(
            isinstance(environment.get(key), str) and environment[key]
            for key in ("os", "python", "platform", "machine")
        ):
            raise ValueError("environment must record local OS, Python, platform and machine")
        results = baseline_manifest["baseline_results"]
        result_keys = {
            "tests", "passed", "failures", "errors", "skipped",
            "ruff_passed", "ruff_exit_code", "pytest_exit_code",
            "capture_method", "capture_host",
        }
        if not isinstance(results, dict) or not result_keys.issubset(results):
            raise ValueError("baseline_results must include the local pytest evidence")
        if (
            not isinstance(results["tests"], int)
            or results["tests"] < 1
            or results["passed"] != results["tests"]
            or results["failures"] != 0
            or results["errors"] != 0
            or results["skipped"] != 0
            or results["ruff_passed"] is not True
            or results["ruff_exit_code"] != 0
            or results["pytest_exit_code"] != 0
            or results["capture_method"] != "local-host-ruff-pytest-v2"
            or not isinstance(results["capture_host"], str)
            or not results["capture_host"]
        ):
            raise ValueError("baseline must contain a fully passing local-host benchmark run")

        experiment_id = uuid.uuid4().hex
        captured_at = utc_now()
        manifest_json = canonical_json(baseline_manifest)
        manifest_hash = sha256_text(manifest_json)
        with self._connection() as db:
            active = db.execute(
                "SELECT experiment_id FROM experiments WHERE status IN ('prepared', 'running')"
            ).fetchone()
            if active:
                raise ValueError("an experiment is already prepared or running")
            db.execute(
                """
                INSERT INTO experiments (
                    experiment_id, name, status, planned_days, created_at
                ) VALUES (?, ?, 'prepared', ?, ?)
                """,
                (experiment_id, name, planned_days, captured_at),
            )
            db.execute(
                """
                INSERT INTO experiment_baselines (
                    experiment_id, captured_at, manifest_sha256, manifest_json
                ) VALUES (?, ?, ?, ?)
                """,
                (experiment_id, captured_at, manifest_hash, manifest_json),
            )
            db.execute(
                """
                INSERT INTO daily_snapshots (
                    snapshot_id, experiment_id, day_index, captured_at,
                    source_commit, manifest_sha256, snapshot_json
                ) VALUES (?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    experiment_id,
                    captured_at,
                    str(baseline_manifest["source_commit"]),
                    manifest_hash,
                    manifest_json,
                ),
            )
        return {
            "experiment_id": experiment_id,
            "status": "prepared",
            "baseline_sha256": manifest_hash,
        }

    def start_experiment(self, experiment_id: str) -> None:
        """Start a prepared experiment; this is a separate, explicit action."""
        with self._connection() as db:
            cursor = db.execute(
                """
                UPDATE experiments
                SET status = 'running', started_at = ?
                WHERE experiment_id = ? AND status = 'prepared'
                """,
                (utc_now(), experiment_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("experiment is missing or is not in prepared state")

    def record_daily_snapshot(
        self,
        *,
        experiment_id: str,
        day_index: int,
        source_commit: str,
        snapshot: dict[str, Any],
    ) -> str:
        if day_index < 1:
            raise ValueError("daily snapshots after the baseline must use day_index >= 1")
        snapshot_id = uuid.uuid4().hex
        snapshot_json = canonical_json(snapshot)
        manifest_hash = sha256_text(snapshot_json)
        with self._connection() as db:
            experiment = db.execute(
                """
                SELECT planned_days, status FROM experiments
                WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchone()
            if not experiment or experiment["status"] != "running":
                raise ValueError("experiment must be running to record a daily snapshot")
            if day_index > experiment["planned_days"]:
                raise ValueError("day_index exceeds the experiment duration")
            try:
                db.execute(
                    """
                    INSERT INTO daily_snapshots (
                        snapshot_id, experiment_id, day_index, captured_at,
                        source_commit, manifest_sha256, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        experiment_id,
                        day_index,
                        utc_now(),
                        source_commit,
                        manifest_hash,
                        snapshot_json,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "a snapshot already exists for this experiment day; "
                    "append a correction event instead of replacing it"
                ) from exc
            if day_index == int(experiment["planned_days"]):
                db.execute(
                    "UPDATE experiments SET status = 'completed' "
                    "WHERE experiment_id = ? AND status = 'running'",
                    (experiment_id,),
                )
        return snapshot_id

    def list_experiments(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute("""
                SELECT e.*, b.manifest_sha256 AS baseline_sha256,
                       (SELECT COUNT(*) FROM daily_snapshots s
                        WHERE s.experiment_id = e.experiment_id) AS snapshot_count
                FROM experiments e
                LEFT JOIN experiment_baselines b
                    ON b.experiment_id = e.experiment_id
                ORDER BY e.created_at DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def get_baseline(self, experiment_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT captured_at, manifest_sha256, manifest_json
                FROM experiment_baselines
                WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchone()
        if row is None:
            return None
        manifest_json = row["manifest_json"]
        if sha256_text(manifest_json) != row["manifest_sha256"]:
            raise ValueError("stored Day 0 baseline hash does not match its manifest")
        try:
            manifest = json.loads(manifest_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored Day 0 baseline manifest is invalid") from exc
        return {
            "captured_at": row["captured_at"],
            "baseline_sha256": row["manifest_sha256"],
            "manifest": manifest,
        }

    def get_snapshot_status(
        self,
        experiment_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT e.experiment_id, e.status, e.planned_days, e.started_at,
                       COALESCE(MAX(s.day_index), 0) AS latest_day_index
                FROM experiments e
                LEFT JOIN daily_snapshots s ON s.experiment_id = e.experiment_id
                WHERE e.experiment_id = ?
                GROUP BY e.experiment_id
                """,
                (experiment_id,),
            ).fetchone()
        if row is None:
            return None
        latest_day = int(row["latest_day_index"])
        started_at = row["started_at"]
        if row["status"] != "running" or not started_at:
            return {
                "experiment_id": experiment_id,
                "status": row["status"],
                "latest_day_index": latest_day,
                "day_index": None,
                "due": False,
                "days_missed": [],
                "reason": "experiment is not running",
            }

        started = datetime.fromisoformat(started_at)
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        elapsed_seconds = max(0, (instant - started).total_seconds())
        elapsed_day = int(elapsed_seconds // 86400)
        due = (
            elapsed_day >= 1
            and elapsed_day <= int(row["planned_days"])
            and elapsed_day > latest_day
        )
        reason = None
        if elapsed_day < 1:
            reason = "the first daily snapshot is due 24 hours after experiment start"
        elif elapsed_day > int(row["planned_days"]):
            reason = "the planned experiment duration has elapsed"
        elif elapsed_day <= latest_day:
            reason = "a snapshot for the current experiment day already exists"
        return {
            "experiment_id": experiment_id,
            "status": row["status"],
            "planned_days": int(row["planned_days"]),
            "started_at": started_at,
            "latest_day_index": latest_day,
            "day_index": elapsed_day if elapsed_day >= 1 else None,
            "due": due,
            "days_missed": (
                list(range(latest_day + 1, elapsed_day)) if due else []
            ),
            "reason": reason,
        }

    def list_snapshots(self, experiment_id: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute("""
                SELECT * FROM daily_snapshots
                WHERE experiment_id = ?
                ORDER BY day_index
            """, (experiment_id,)).fetchall()
        return [dict(row) for row in rows]

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
                event_id, utc_now(), experiment_id, day_index, layer_id,
                category, event_type, outcome, summary,
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
                sample_id, utc_now(), experiment_id, day_index, metric_name,
                float(value), unit, benchmark_id, source_event_id,
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
            experiment = db.execute("""
                SELECT experiment_id, name, status, planned_days, started_at
                FROM experiments
                WHERE status IN ('prepared', 'running')
                ORDER BY created_at DESC LIMIT 1
            """).fetchone()
        return {
            "event_count": event_count,
            "metric_sample_count": metric_count,
            "latest_day_index": latest_day,
            "latest_event_at": latest_event,
            "experiment_id": experiment["experiment_id"] if experiment else None,
            "experiment_name": experiment["name"] if experiment else None,
            "experiment_status": experiment["status"] if experiment else "not_started",
            "planned_days": experiment["planned_days"] if experiment else None,
            "data_status": (
                "recorded" if event_count or metric_count else "awaiting_baseline"
            ),
        }
