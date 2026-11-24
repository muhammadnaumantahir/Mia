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

from .measurements import validate_sample


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

                CREATE TRIGGER IF NOT EXISTS immutable_metric_update
                BEFORE UPDATE ON metric_samples
                BEGIN
                    SELECT RAISE(ABORT, 'metric sample is immutable');
                END;

                CREATE TRIGGER IF NOT EXISTS immutable_metric_delete
                BEFORE DELETE ON metric_samples
                BEGIN
                    SELECT RAISE(ABORT, 'metric sample is immutable');
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
            "fixed_benchmark_results",
            "runtime_settings",
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
        for section in ("dependencies", "resource_sample"):
            value = baseline_manifest.get(section)
            if value is not None and not (isinstance(value, dict) and isinstance(value.get("available"), bool)):
                raise ValueError(f"{section} must state whether it is available")
        if baseline_manifest.get("not_captured") is not None and not isinstance(
            baseline_manifest["not_captured"], dict
        ):
            raise ValueError("not_captured must map each missing item to a reason")
        runtime_settings = baseline_manifest["runtime_settings"]
        required_runtime_settings = {
            "MIA_EXPERIMENT_AUTO_CAPTURE", "MIA_AUTONOMY_ENABLED",
            "MIA_EMERGENCY_STOP", "MIA_ALLOW_PAID", "MIA_MAX_CPU_PERCENT",
            "MIA_MAX_RAM_PERCENT", "MIA_AUTONOMY_INTERVAL_SECONDS",
            "MIA_APPLICATIONS_CONFIG_PRESENT", "MIA_APPLICATIONS_CONFIG_SHA256",
        }
        if not isinstance(runtime_settings, dict) or not required_runtime_settings.issubset(
            runtime_settings
        ):
            raise ValueError("runtime_settings must include the non-secret experiment policy")
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
            or results["capture_method"] != "local-host-fixed-benchmark-and-regression-v3"
            or not isinstance(results["capture_host"], str)
            or not results["capture_host"]
        ):
            raise ValueError("baseline must contain a fully passing local-host benchmark run")

        fixed = baseline_manifest["fixed_benchmark_results"]
        fixed_keys = {
            "tests", "passed", "failures", "errors", "skipped",
            "duration_seconds", "pytest_exit_code", "suite_id",
        }
        if not isinstance(fixed, dict) or not fixed_keys.issubset(fixed):
            raise ValueError("fixed_benchmark_results must include versioned JUnit evidence")
        if (
            fixed["suite_id"] != baseline_manifest["benchmark_suite_id"]
            or fixed["suite_id"] != "mia-foundation-v2"
            or not isinstance(fixed["tests"], int)
            or fixed["tests"] < 1
            or fixed["passed"] != fixed["tests"]
            or fixed["failures"] != 0
            or fixed["errors"] != 0
            or fixed["skipped"] != 0
            or fixed["pytest_exit_code"] != 0
        ):
            raise ValueError("fixed benchmark must pass every local benchmark check")
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
                    """
                    UPDATE experiments
                    SET status = 'completed', completed_at = ?
                    WHERE experiment_id = ? AND status = 'running'
                    """,
                    (utc_now(), experiment_id),
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
        """Return capture readiness and close runs that missed their final day.

        The final planned day remains eligible for capture for its full 24-hour
        window. Once that window has passed, a still-running experiment becomes
        terminally incomplete; it is never reported as completed.
        """
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)

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
            status = str(row["status"])
            planned_days = int(row["planned_days"])
            if status != "running" or not started_at:
                return {
                    "experiment_id": experiment_id,
                    "status": status,
                    "planned_days": planned_days,
                    "started_at": started_at,
                    "latest_day_index": latest_day,
                    "day_index": None,
                    "due": False,
                    "days_missed": [],
                    "reason": (
                        "the experiment is incomplete; its final daily snapshot "
                        "was not captured"
                        if status == "incomplete"
                        else "experiment is not running"
                    ),
                }

            started = datetime.fromisoformat(started_at)
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed_seconds = max(0, (instant - started).total_seconds())
            elapsed_day = int(elapsed_seconds // 86400)

            if elapsed_day > planned_days:
                cursor = db.execute(
                    """
                    UPDATE experiments
                    SET status = 'incomplete', completed_at = ?
                    WHERE experiment_id = ? AND status = 'running'
                    """,
                    (instant.isoformat(), experiment_id),
                )
                if cursor.rowcount == 1:
                    db.execute(
                        """
                        INSERT INTO events (
                            event_id, occurred_at, experiment_id, day_index,
                            layer_id, category, event_type, outcome, summary,
                            payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            uuid.uuid4().hex,
                            instant.isoformat(),
                            experiment_id,
                            planned_days,
                            "SPS-09",
                            "experiment",
                            "experiment_incomplete",
                            "incomplete",
                            "Experiment duration elapsed before the final daily evidence was captured",
                            json.dumps(
                                {
                                    "planned_days": planned_days,
                                    "latest_recorded_day": latest_day,
                                    "elapsed_day": elapsed_day,
                                    "final_day_snapshot_missing": latest_day < planned_days,
                                },
                                sort_keys=True,
                            ),
                        ),
                    )
                    status = "incomplete"
                else:
                    current = db.execute(
                        "SELECT status FROM experiments WHERE experiment_id = ?",
                        (experiment_id,),
                    ).fetchone()
                    status = str(current["status"]) if current else "unknown"

                return {
                    "experiment_id": experiment_id,
                    "status": status,
                    "planned_days": planned_days,
                    "started_at": started_at,
                    "latest_day_index": latest_day,
                    "day_index": None,
                    "due": False,
                    "days_missed": [],
                    "reason": (
                        "the experiment is incomplete because the final daily "
                        "snapshot was not captured within its scheduled window"
                        if status == "incomplete"
                        else "experiment is not running"
                    ),
                }

            due = elapsed_day >= 1 and elapsed_day > latest_day
            reason = None
            if elapsed_day < 1:
                reason = "the first daily snapshot is due 24 hours after experiment start"
            elif elapsed_day <= latest_day:
                reason = "a snapshot for the current experiment day already exists"
            return {
                "experiment_id": experiment_id,
                "status": status,
                "planned_days": planned_days,
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

    @staticmethod
    def _aggregate_activity(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        """Sum daily activity evidence; unreadable days are listed, never counted as zero."""
        usage_keys = ("requests", "input_tokens", "output_tokens", "estimated_token_rows",
                      "paid_requests", "non_local_requests")
        totals: dict[str, Any] = {
            "days_with_activity_evidence": 0,
            "days_without_activity_evidence": [],
            "unavailable_sources": {},
            "inference_usage": {key: 0 for key in usage_keys} | {"cost_usd": 0.0},
            "capabilities": {"transitions": {}, "owner_approved_actions": 0},
            "tasks": {"transitions": {}, "owner_actions": 0,
                      "restart_recoveries": 0, "lease_recoveries": 0},
        }
        for snapshot in snapshots:
            day = snapshot["day_index"]
            if day == 0:
                continue
            activity = snapshot["evidence"].get("activity")
            if not isinstance(activity, dict) or "window" not in activity:
                totals["days_without_activity_evidence"].append(day)
                continue
            totals["days_with_activity_evidence"] += 1
            for source in ("inference_usage", "capabilities", "tasks"):
                data = activity.get(source)
                if not isinstance(data, dict) or data.get("available") is not True:
                    totals["unavailable_sources"].setdefault(source, []).append(day)
                    continue
                target = totals[source]
                for key, value in data.items():
                    if key == "available":
                        continue
                    if key == "latency_ms":
                        # Medians and percentiles are not additive: keep them per day.
                        target.setdefault("daily_latency_ms", []).append(
                            {"day_index": day, **value}
                        )
                        continue
                    if isinstance(value, dict):
                        for name, count in value.items():
                            target[key][name] = target[key].get(name, 0) + count
                    elif isinstance(value, int | float) and not isinstance(value, bool):
                        target[key] = target.get(key, 0) + value
        totals["inference_usage"]["cost_usd"] = round(totals["inference_usage"]["cost_usd"], 8)
        return totals

    def build_experiment_report(self, experiment_id: str) -> dict[str, Any] | None:
        """Build an integrity-checked report from evidence tied to one experiment."""
        # Close an expired run even when no dashboard status poll or scheduler
        # cycle has happened since its final daily capture window elapsed.
        self.get_snapshot_status(experiment_id)
        with self._connection() as db:
            experiment = db.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            if experiment is None:
                return None
            baseline_row = db.execute(
                """
                SELECT captured_at, manifest_sha256, manifest_json
                FROM experiment_baselines WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchone()
            snapshot_rows = db.execute(
                """
                SELECT * FROM daily_snapshots
                WHERE experiment_id = ? ORDER BY day_index
                """,
                (experiment_id,),
            ).fetchall()
            event_rows = db.execute(
                """
                SELECT * FROM events WHERE experiment_id = ?
                ORDER BY occurred_at, event_id
                """,
                (experiment_id,),
            ).fetchall()
            metric_rows = db.execute(
                """
                SELECT * FROM metric_samples WHERE experiment_id = ?
                ORDER BY day_index, occurred_at, sample_id
                """,
                (experiment_id,),
            ).fetchall()

        if baseline_row is None:
            raise ValueError("experiment baseline is missing")
        baseline_json = str(baseline_row["manifest_json"])
        if sha256_text(baseline_json) != baseline_row["manifest_sha256"]:
            raise ValueError("stored Day 0 baseline hash does not match its manifest")
        try:
            baseline_manifest = json.loads(baseline_json)
        except json.JSONDecodeError as exc:
            raise ValueError("stored Day 0 baseline manifest is invalid") from exc

        snapshots: list[dict[str, Any]] = []
        unavailable_days: set[int] = set()
        for row in snapshot_rows:
            snapshot_json = str(row["snapshot_json"])
            if sha256_text(snapshot_json) != row["manifest_sha256"]:
                raise ValueError(
                    f"stored Day {row['day_index']} snapshot hash does not match its evidence"
                )
            try:
                evidence = json.loads(snapshot_json)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"stored Day {row['day_index']} snapshot is invalid"
                ) from exc
            if int(row["day_index"]) == 0:
                metadata_matches = (
                    evidence == baseline_manifest
                    and row["manifest_sha256"] == baseline_row["manifest_sha256"]
                    and evidence.get("source_commit") == row["source_commit"]
                )
            else:
                metadata_matches = (
                    evidence.get("day_index") == int(row["day_index"])
                    and evidence.get("source_commit") == row["source_commit"]
                )
            if not metadata_matches:
                raise ValueError(
                    f"stored Day {row['day_index']} snapshot metadata is inconsistent"
                )
            if int(row["day_index"]) > 0:
                snapshot_missing = evidence.get("missing_day_indices", [])
                if isinstance(snapshot_missing, list):
                    unavailable_days.update(
                        day for day in snapshot_missing
                        if isinstance(day, int) and not isinstance(day, bool)
                        and 1 <= day <= int(experiment["planned_days"])
                    )
            snapshots.append({
                "snapshot_id": row["snapshot_id"],
                "day_index": int(row["day_index"]),
                "captured_at": row["captured_at"],
                "source_commit": row["source_commit"],
                "manifest_sha256": row["manifest_sha256"],
                "evidence": evidence,
            })

        events: list[dict[str, Any]] = []
        event_counts: dict[str, int] = {}
        for row in event_rows:
            item = dict(row)
            try:
                payload = json.loads(item["payload_json"])
            except json.JSONDecodeError as exc:
                raise ValueError("stored experiment event payload is invalid") from exc
            item["payload"] = payload
            events.append(item)
            event_counts[item["event_type"]] = event_counts.get(item["event_type"], 0) + 1
            if item["event_type"] == "daily_snapshot_skipped_paused":
                # An owner-paused day is explained by its own event: explicit, not unaccounted.
                paused_day = item.get("day_index")
                if (isinstance(paused_day, int) and not isinstance(paused_day, bool)
                        and 1 <= paused_day <= int(experiment["planned_days"])):
                    unavailable_days.add(paused_day)
            if item["event_type"] == "daily_snapshots_unavailable":
                days = payload.get("missing_day_indices", [])
                if isinstance(days, list):
                    unavailable_days.update(
                        day for day in days
                        if isinstance(day, int) and not isinstance(day, bool)
                        and 1 <= day <= int(experiment["planned_days"])
                    )

        metrics: list[dict[str, Any]] = []
        metric_series: dict[str, list[dict[str, Any]]] = {}
        for row in metric_rows:
            item = dict(row)
            try:
                item["context"] = json.loads(item.pop("context_json"))
            except json.JSONDecodeError as exc:
                raise ValueError("stored experiment metric context is invalid") from exc
            metrics.append(item)
            metric_series.setdefault(item["metric_name"], []).append({
                "day_index": item["day_index"],
                "value": item["value"],
                "unit": item["unit"],
                "benchmark_id": item["benchmark_id"],
                "occurred_at": item["occurred_at"],
            })

        planned_days = int(experiment["planned_days"])
        recorded_days = sorted({item["day_index"] for item in snapshots})
        captured_daily_days = set(recorded_days).difference({0})
        missing_days = set(range(1, planned_days + 1)).difference(captured_daily_days)
        unavailable_days.intersection_update(missing_days)
        unaccounted_days = sorted(missing_days.difference(unavailable_days))
        final_snapshot = next(
            (item for item in snapshots if item["day_index"] == planned_days),
            None,
        )
        latest_snapshot = snapshots[-1] if snapshots else None
        status = str(experiment["status"])
        if status == "completed" and final_snapshot is None:
            raise ValueError(
                "experiment is marked completed but its final planned-day snapshot is missing"
            )

        comparison_snapshot = (
            final_snapshot
            if status == "completed"
            else (
                latest_snapshot
                if latest_snapshot is not None and latest_snapshot["day_index"] > 0
                else None
            )
        )
        baseline_results = baseline_manifest.get("baseline_results", {})
        baseline_benchmark = baseline_manifest.get("fixed_benchmark_results", {})
        comparison_results = (
            comparison_snapshot["evidence"].get("fixed_benchmark_results", {})
            if comparison_snapshot else {}
        )

        def pass_rate(results: dict[str, Any]) -> float | None:
            total = results.get("tests")
            passed = results.get("passed")
            if not isinstance(total, int) or total <= 0 or not isinstance(passed, int):
                return None
            return passed / total * 100.0

        baseline_rate = pass_rate(baseline_benchmark)
        comparison_rate = pass_rate(comparison_results)
        delta_rate = (
            comparison_rate - baseline_rate
            if baseline_rate is not None and comparison_rate is not None
            else None
        )
        baseline_benchmark_duration = baseline_benchmark.get("duration_seconds")
        comparison_benchmark_duration = comparison_results.get("duration_seconds")
        duration_delta = (
            float(comparison_benchmark_duration) - float(baseline_benchmark_duration)
            if isinstance(comparison_benchmark_duration, (int, float))
            and isinstance(baseline_benchmark_duration, (int, float))
            else None
        )

        activity_totals = self._aggregate_activity(snapshots)

        if status == "completed":
            conclusion = "completed"
        elif status == "incomplete":
            conclusion = "incomplete"
        elif status == "running":
            conclusion = "in_progress"
        else:
            conclusion = "not_started"

        return {
            "experiment": {
                "experiment_id": experiment_id,
                "name": experiment["name"],
                "status": status,
                "conclusion": conclusion,
                "planned_days": planned_days,
                "created_at": experiment["created_at"],
                "started_at": experiment["started_at"],
                "completed_at": experiment["completed_at"],
            },
            "report_ready": status in {"completed", "incomplete"},
            "baseline": {
                "captured_at": baseline_row["captured_at"],
                "sha256": baseline_row["manifest_sha256"],
                "manifest": baseline_manifest,
            },
            "days": {
                "recorded": recorded_days,
                "missing": sorted(missing_days),
                "unavailable": sorted(unavailable_days),
                "unaccounted": unaccounted_days,
                "latest_recorded": latest_snapshot["day_index"] if latest_snapshot else 0,
                "final_snapshot_present": final_snapshot is not None,
            },
            "comparison": {
                "compared_day": comparison_snapshot["day_index"] if comparison_snapshot else None,
                                "benchmark_suite_id": baseline_manifest.get("benchmark_suite_id"),
                "baseline_fixed_benchmark": baseline_benchmark,
                "latest_or_final_fixed_benchmark": comparison_results,
                "baseline_full_regression": baseline_results,
                "latest_or_final_full_regression": (
                    comparison_snapshot["evidence"].get("verification", {})
                    if comparison_snapshot else {}
                ),
                "latest_or_final_tests": comparison_results,
                "baseline_test_pass_rate": baseline_rate,
                "latest_or_final_test_pass_rate": comparison_rate,
                "test_pass_rate_change_points": delta_rate,
                "baseline_fixed_benchmark_duration_seconds": baseline_benchmark_duration,
                "latest_or_final_fixed_benchmark_duration_seconds": comparison_benchmark_duration,
                "fixed_benchmark_duration_change_seconds": duration_delta,
                "comparison_is_final": status == "completed" and final_snapshot is not None,
            },
            "snapshots": snapshots,
            "metrics": metrics,
            "metric_series": metric_series,
            "events": events,
            "event_counts": event_counts,
            "activity_totals": activity_totals,
            "capability_attribution": {
                "available": False,
                "reason": (
                    "Capability versions are stored in a separate registry and are not "
                    "linked to experiment IDs; they are not attributed by time alone."
                ),
            },
            "integrity": {
                "baseline_verified": True,
                "snapshots_verified": True,
                "snapshot_count": len(snapshots),
                "event_count": len(events),
                "metric_count": len(metrics),
            },
        }


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
        meta = validate_sample(metric_name, value, unit)
        context = {**(context or {}), **meta}
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
                json.dumps(context, sort_keys=True),
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

    def find_events(
        self,
        event_types: tuple[str, ...] | list[str],
        *,
        experiment_id: str | None = None,
        day_index: int | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Newest-first events of the given types, optionally narrowed (read-only)."""
        types = list(event_types)
        if not types:
            return []
        clauses = [f"event_type IN ({','.join('?' for _ in types)})"]
        params: list[Any] = list(types)
        if experiment_id is not None:
            clauses.append("experiment_id = ?")
            params.append(experiment_id)
        if day_index is not None:
            clauses.append("day_index = ?")
            params.append(day_index)
        if since is not None:
            clauses.append("occurred_at >= ?")
            params.append(since)
        params.append(max(1, min(int(limit), 1000)))
        with self._connection() as db:
            rows = db.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} "
                "ORDER BY occurred_at DESC, rowid DESC LIMIT ?",
                params,
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
