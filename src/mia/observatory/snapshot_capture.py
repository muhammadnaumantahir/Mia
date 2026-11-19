"""Host-generated daily evidence capture for the 30-day Mia experiment."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .baseline import BaselineCaptureError
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
        activity_collector: Any | None = None,
    ) -> None:
        self.activity_collector = activity_collector
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
        except BaselineCaptureError as exc:
            raise SnapshotCaptureError(503, str(exc)) from exc

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

        fixed_benchmark = manifest.get("fixed_benchmark_results")
        baseline_fixed = baseline_manifest.get("fixed_benchmark_results")
        if not isinstance(fixed_benchmark, dict) or not isinstance(baseline_fixed, dict):
            raise SnapshotCaptureError(
                422, "Fixed benchmark evidence is missing; no daily snapshot was stored"
            )
        if (
            fixed_benchmark.get("suite_id") != baseline_fixed.get("suite_id")
            or fixed_benchmark.get("pytest_exit_code") != 0
            or fixed_benchmark.get("tests", 0) <= 0
            or fixed_benchmark.get("passed") != fixed_benchmark.get("tests")
            or fixed_benchmark.get("failures") != 0
            or fixed_benchmark.get("errors") != 0
            or fixed_benchmark.get("skipped") != 0
        ):
            raise SnapshotCaptureError(
                422, "Fixed benchmark suite did not pass; no daily snapshot was stored"
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
        # Activity window: since the previous daily snapshot, else the experiment start
        # (the Day 0 baseline predates the start and is not part of the window).
        previous = [
            item for item in self.observatory.list_snapshots(experiment_id)
            if int(item["day_index"]) > 0
        ]
        window_start = (
            previous[-1]["captured_at"] if previous else (due.get("started_at") or captured_at)
        )
        if self.activity_collector is None:
            activity: dict[str, Any] = {"available": False, "reason": "collector not configured"}
        else:
            activity = self.activity_collector.collect(window_start, captured_at)
        snapshot = {
            "captured_at": captured_at,
            "day_index": day_index,
            "baseline_sha256": baseline["baseline_sha256"],
            "source_commit": manifest["source_commit"],
            "configuration_hash": manifest["configuration_hash"],
            "benchmark_suite_id": manifest["benchmark_suite_id"],
            "environment": manifest["environment"],
            "runtime_settings": manifest["runtime_settings"],
            "verification": manifest["baseline_results"],
            "fixed_benchmark_results": manifest["fixed_benchmark_results"],
            "resources": resources,
            "verification_passed": True,
            "missing_day_indices": due["days_missed"],
            "activity": activity,
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
                "fixed_benchmark_tests": manifest["fixed_benchmark_results"]["tests"],
                "fixed_benchmark_duration_seconds": manifest["fixed_benchmark_results"]["duration_seconds"],
                "ruff_passed": manifest["baseline_results"]["ruff_passed"],
                "cpu_percent": cpu,
                "memory_percent": memory,
                "missing_day_indices": due["days_missed"],
            },
        )
        total_tests = int(manifest["baseline_results"]["tests"])
        passed_tests = int(manifest["baseline_results"]["passed"])
        test_pass_rate = (passed_tests / total_tests * 100.0) if total_tests else 0.0
        fixed_tests = int(manifest["fixed_benchmark_results"]["tests"])
        fixed_passed = int(manifest["fixed_benchmark_results"]["passed"])
        fixed_pass_rate = fixed_passed / fixed_tests * 100.0 if fixed_tests else 0.0
        for metric_name, value in (
            ("cpu_percent", cpu),
            ("memory_percent", memory),
            ("test_pass_rate", test_pass_rate),
            ("fixed_benchmark_pass_rate", fixed_pass_rate),
            ("fixed_benchmark_duration_seconds", float(manifest["fixed_benchmark_results"]["duration_seconds"])),
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

        usage = activity.get("inference_usage", {})
        if usage.get("available"):
            for metric_name, value, unit in (
                ("inference_cost_usd", float(usage["cost_usd"]), "usd"),
                ("inference_requests", float(usage["requests"]), "count"),
                ("inference_output_tokens", float(usage["output_tokens"]), "tokens"),
            ):
                self.observatory.record_metric(
                    day_index=day_index,
                    metric_name=metric_name,
                    value=value,
                    unit=unit,
                    benchmark_id=None,
                    experiment_id=experiment_id,
                    source_event_id=event_id,
                    context={"source": "usage_ledger", "window": activity["window"]},
                )
        total_latency = (usage.get("latency_ms") or {}).get("total") or {}
        if usage.get("available") and total_latency.get("median") is not None:
            for metric_name, key in (
                ("inference_latency_median_ms", "median"),
                ("inference_latency_p95_ms", "p95"),
            ):
                self.observatory.record_metric(
                    day_index=day_index,
                    metric_name=metric_name,
                    value=float(total_latency[key]),
                    unit="ms",
                    benchmark_id=None,
                    experiment_id=experiment_id,
                    source_event_id=event_id,
                    context={
                        "source": "usage_ledger",
                        "measured_requests": total_latency["count"],
                        "window": activity["window"],
                    },
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
