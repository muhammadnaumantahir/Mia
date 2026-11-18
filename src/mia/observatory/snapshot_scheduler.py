"""Local, resource-aware daily evidence scheduler."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .snapshot_capture import ExperimentSnapshotCapturer, SnapshotCaptureError
from .store import ObservatoryStore


class ExperimentSnapshotScheduler:
    """Capture due snapshots while Mia is running, without inventing missed days."""

    def __init__(
        self,
        observatory: ObservatoryStore,
        capturer: ExperimentSnapshotCapturer,
        resource_monitor: Any,
        *,
        enabled: bool = False,
        interval_seconds: int = 60,
        retry_seconds: int = 900,
        max_cpu_percent: float = 75.0,
        max_memory_percent: float = 85.0,
    ) -> None:
        if not 15 <= interval_seconds <= 3600:
            raise ValueError("interval_seconds must be between 15 and 3600")
        if not 60 <= retry_seconds <= 7200:
            raise ValueError("retry_seconds must be between 60 and 7200")
        if not 1 <= max_cpu_percent <= 100 or not 1 <= max_memory_percent <= 100:
            raise ValueError("resource thresholds must be between 1 and 100")
        self.observatory = observatory
        self.capturer = capturer
        self.resource_monitor = resource_monitor
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.retry_seconds = retry_seconds
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_percent = max_memory_percent
        self._last_attempt: dict[str, float] = {}
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval_seconds,
            "retry_seconds": self.retry_seconds,
            "max_cpu_percent": self.max_cpu_percent,
            "max_memory_percent": self.max_memory_percent,
        }

    async def run_once(self) -> list[dict[str, Any]]:
        """Check all running experiments and process due snapshots once."""
        if not self.enabled:
            return []
        outcomes: list[dict[str, Any]] = []
        experiments = self.observatory.list_experiments()
        for experiment in experiments:
            if experiment["status"] != "running":
                continue
            experiment_id = experiment["experiment_id"]
            due = self.observatory.get_snapshot_status(experiment_id)
            if due is None or not due["due"]:
                continue
            now = time.monotonic()
            last_attempt = self._last_attempt.get(experiment_id)
            if last_attempt is not None and now - last_attempt < self.retry_seconds:
                continue
            self._last_attempt[experiment_id] = now

            try:
                resources = await asyncio.to_thread(self.resource_monitor.snapshot)
                cpu = float(resources["cpu_percent"])
                memory = float(resources["memory_percent"])
            except (RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
                outcomes.append({
                    "experiment_id": experiment_id,
                    "status": "deferred",
                    "reason": f"resource telemetry unavailable: {type(exc).__name__}",
                })
                continue
            if (
                not 0 <= cpu <= 100
                or not 0 <= memory <= 100
                or cpu >= self.max_cpu_percent
                or memory >= self.max_memory_percent
            ):
                self.observatory.record_event(
                    category="experiment",
                    event_type="daily_snapshot_deferred_resource_pressure",
                    summary=f"Daily capture deferred on Day {due['day_index']} due to resource pressure",
                    outcome="deferred",
                    day_index=due["day_index"],
                    experiment_id=experiment_id,
                    layer_id="SPS-09",
                    payload={
                        "cpu_percent": cpu,
                        "memory_percent": memory,
                        "max_cpu_percent": self.max_cpu_percent,
                        "max_memory_percent": self.max_memory_percent,
                    },
                )
                outcomes.append({
                    "experiment_id": experiment_id,
                    "status": "deferred",
                    "reason": "resource_limit",
                })
                continue

            try:
                result = await asyncio.to_thread(self.capturer.capture, experiment_id)
            except SnapshotCaptureError as exc:
                self.observatory.record_event(
                    category="experiment",
                    event_type="daily_snapshot_auto_capture_failed",
                    summary=f"Automatic daily capture failed on Day {due['day_index']}",
                    outcome="failed",
                    day_index=due["day_index"],
                    experiment_id=experiment_id,
                    layer_id="SPS-09",
                    payload={
                        "status_code": exc.status_code,
                        "detail": exc.detail if isinstance(exc.detail, str) else exc.detail,
                    },
                )
                outcomes.append({
                    "experiment_id": experiment_id,
                    "status": "failed",
                    "reason": str(exc),
                })
            else:
                self._last_attempt.pop(experiment_id, None)
                outcomes.append({
                    "experiment_id": experiment_id,
                    "status": result["status"],
                    "day_index": result["day_index"],
                    "snapshot_id": result["snapshot_id"],
                })
        return outcomes

    async def run(self) -> None:
        """Run until FastAPI shuts down; only active, explicitly-started experiments qualify."""
        if not self.enabled:
            return
        self._stop_event = asyncio.Event()
        try:
            while not self._stop_event.is_set():
                try:
                    await self.run_once()
                except Exception as exc:
                    self.observatory.record_event(
                        category="experiment",
                        event_type="daily_snapshot_scheduler_error",
                        summary="Automatic daily snapshot scheduler cycle failed",
                        outcome="failed",
                        layer_id="SPS-09",
                        payload={"error_type": type(exc).__name__},
                    )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            self._stop_event = None

    def start(self) -> asyncio.Task[None] | None:
        """Start one scheduler task; duplicate starts return the active task."""
        if not self.enabled:
            return None
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self.run(), name="mia-experiment-snapshot-scheduler")
        return self._task

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        try:
            await task
        finally:
            self._task = None
