"""Host-generated daily evidence capture for the 30-day Mia experiment."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from .store import ObservatoryStore


class SnapshotCaptureError(RuntimeError):
    """A daily snapshot could not be safely captured."""

    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class ExperimentSnapshotCapturer:
    """Capture one due day from verified local source, tests and host telemetry."""

    def __init__(
        self,
        *,
        observatory: ObservatoryStore,
        baseline_capture: Callable[[str | Path], dict[str, Any]],
        resource_monitor: Any,
        repo_root: str | Path,
    ) -> None:
        self.observatory = observatory
        self.baseline_capture = baseline_capture
        self.resource_monitor = resource_monitor
        self.repo_root = Path(repo_root).resolve()

    def capture(self, experiment_id: str) -> dict[str, Any]:
        due = self.observatory.get_snapshot_status(experiment_id)
        if due is None:
            raise SnapshotCaptureError(404, "Experiment not found")
        if not due["due"]:
            raise SnapshotCaptureError(
                409,
                {
                    "message": due.get("reason") or "No daily snapshot is due",
                    "snapshot_status": due,
                },
            )

        baseline = self.observatory.get_baseline(experiment_id)
        if baseline is None:
            raise SnapshotCaptureError(409, "Experiment baseline is missing")
        baseline_manifest = baseline["manifest"]

        try:
            manifest = self.baseline_capture(self.repo_root)
        except Exception as exc:
            from .baseline import BaselineCaptureError

            if isinstance(exc, BaselineCaptureError):
                raise SnapshotCaptureError(503, str(exc)) from exc
            raise

        if manifest.get("verification_passed") is not True:
            results = manifest.get("baseline_results", {})
            self.observatory.record_event(
                category="experiment",
                event_type="daily_snapshot_verification_failed",
                summary=f"Day {due['day_index']} verification failed; no snapshot stored",
                outcome="failed",
                day_index=due["day_index"],
                experiment_id=experiment_id,
                layer_id="SPS-09",
                payload={"baseline_results": results},
            )
            raise SnapshotCaptureError(
                422,
                {
                    "message": "Daily Ruff/test verification did not pass; no snapshot was stored",
                    "baseline_results": results,
                },
            )

        if (
            manifest.get("benchmark_suite_id") != baseline_manifest.get("benchmark_suite_id")
            or manifest.get("configuration_hash") != baseline_manifest.get("configuration_hash")
        ):
            raise SnapshotCaptureError(
                409,
                "Daily benchmark suite or configuration differs from immutable Day 0",
            )
        baseline_environment = baseline_manifest.get("environment", {})
        current_environment = manifest.get("environment", {})
        for key in ("os", "python", "machine"):
            if current_environment.get(key) != baseline_environment.get(key):
                raise SnapshotCaptureError(
                    409,
                    f"Daily environment changed from Day 0 ({key})",
                )

        # Recheck after the full suite to avoid storing a snapshot for an
        # outdated day or racing another capture request.
        current_due = self.observatory.get_snapshot_status(experiment_id)
        if (
            current_due is None
            or not current_due["due"]
            or current_due["day_index"] != due["day_index"]
        ):
            raise SnapshotCaptureError(
                409,
                "Daily snapshot due state changed while verification was running; refresh and retry",
            )

        try:
            resources = self.resource_monitor.snapshot()
            cpu = float(resources["cpu_percent"])
            memory = float(resources["memory_percent"])
        except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            raise SnapshotCaptureError(
                503,
                "Local CPU/RAM telemetry is unavailable for this daily snapshot",
            ) from exc
        if not (0 <= cpu <= 100 and 0 <= memory <= 100):
            raise SnapshotCaptureError(503, "Local CPU/RAM telemetry returned invalid values")

        day_index = int(due["day_index"])
        captured_at = datetime.now(UTC).isoformat()
        snapshot = {
            "captured_at": captured_at,
            "day_index": day_index,
            "baseline_sha256": baseline["baseline_sha256"],
            "source_commit": manifest["source_commit"],
            "configuration_hash": manifest["configuration_hash"],
            "benchmark_suite_id": manifest["benchmark_suite_id"],
            "environment": manifest["environment"],
            "verification": manifest["baseline_results"],
            "resources": resources,
            "verification_passed": True,
            "missing_day_indices": due["days_missed"],
        }
        try:
            snapshot_id = self.observatory.record_daily_snapshot(
                experiment_id=experiment_id,
                day_index=day_index,
                source_commit=manifest["source_commit"],
                snapshot=snapshot,
            )
        except ValueError as exc:
            raise SnapshotCaptureError(409, str(exc)) from exc

        if due["days_missed"]:
            self.observatory.record_event(
                category="experiment",
                event_type="daily_snapshots_unavailable",
                summary=f"Days {due['days_missed']} were not captured while Mia was offline",
                outcome="unavailable",
                day_index=day_index,
                experiment_id=experiment_id,
                layer_id="SPS-09",
                payload={"missing_day_indices": due["days_missed"]},
            )
        event_id = self.observatory.record_event(
            category="experiment",
            event_type="daily_snapshot_captured",
            summary=f"Verified Day {day_index} evidence captured on the Mia host",
            outcome="completed" if day_index == due["planned_days"] else "recorded",
            day_index=day_index,
            experiment_id=experiment_id,
            layer_id="SPS-09",
            payload={
                "snapshot_id": snapshot_id,
                "source_commit": manifest["source_commit"],
                "tests": manifest["baseline_results"]["tests"],
                "passed": manifest["baseline_results"]["passed"],
                "ruff_passed": manifest["baseline_results"]["ruff_passed"],
                "cpu_percent": cpu,
                "memory_percent": memory,
                "missing_day_indices": due["days_missed"],
            },
        )
        total_tests = int(manifest["baseline_results"]["tests"])
        passed_tests = int(manifest["baseline_results"]["passed"])
        test_pass_rate = (passed_tests / total_tests * 100.0) if total_tests else 0.0
        for metric_name, value in (
            ("cpu_percent", cpu),
            ("memory_percent", memory),
            ("test_pass_rate", test_pass_rate),
        ):
            self.observatory.record_metric(
                day_index=day_index,
                metric_name=metric_name,
                value=value,
                unit="percent",
                benchmark_id=manifest["benchmark_suite_id"],
                experiment_id=experiment_id,
                source_event_id=event_id,
                context={"source": "local_host_verification"},
            )

        return {
            "snapshot_id": snapshot_id,
            "experiment_id": experiment_id,
            "day_index": day_index,
            "status": "completed" if day_index == due["planned_days"] else "recorded",
            "source_commit": manifest["source_commit"],
            "resources": resources,
            "verification": manifest["baseline_results"],
            "missing_day_indices": due["days_missed"],
        }
