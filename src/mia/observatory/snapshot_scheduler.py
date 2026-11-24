"""Local, resource-aware daily evidence scheduler."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from .snapshot_capture import ExperimentSnapshotCapturer, SnapshotCaptureError
from .store import ObservatoryStore

PAUSED_EVENT = "daily_snapshot_scheduler_paused"
RESUMED_EVENT = "daily_snapshot_scheduler_resumed"
SKIPPED_PAUSED_EVENT = "daily_snapshot_skipped_paused"
TELEMETRY_DEFERRED_EVENT = "daily_snapshot_deferred_telemetry_unavailable"
NOOP_DAY_EVENT = "daily_snapshot_scheduler_noop_day"


class SchedulerNotEnabledError(RuntimeError):
    """Pause/resume only make sense for a scheduler the owner opted in to."""


class ExperimentSnapshotScheduler:
    """Capture due snapshots while Mia is running, without inventing missed days.

    Every day the scheduler could not or did not capture leaves an explicit, durable event
    (failed, deferred for resource pressure or missing telemetry, skipped while paused, a
    no-op day with nothing running) so a gap in the evidence is never silent. Days Mia was not
    running are recorded by the capturer at the next capture. Pause state is the newest
    paused/resumed event, so it survives restarts and shows in the audit trail.
    """

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
        self._noop_day: str | None = None

    # ---- owner pause / resume (durable: the newest paused/resumed event wins) -------------

    def _latest_pause_event(self) -> dict[str, Any] | None:
        events = self.observatory.find_events((PAUSED_EVENT, RESUMED_EVENT), limit=1)
        return events[0] if events else None

    @property
    def paused(self) -> bool:
        latest = self._latest_pause_event()
        return latest is not None and latest["event_type"] == PAUSED_EVENT

    def pause(self) -> bool:
        """Pause capture. Returns False if it was already paused. Needs an opted-in scheduler."""
        return self._set_paused(True)

    def resume(self) -> bool:
        """Resume capture. Returns False if it was not paused."""
        return self._set_paused(False)

    def _set_paused(self, paused: bool) -> bool:
        if not self.enabled:
            raise SchedulerNotEnabledError(
                "The daily snapshot scheduler is not enabled (opt in with MIA_EXPERIMENT_AUTO_CAPTURE=true)"
            )
        if self.paused == paused:
            return False
        self.observatory.record_event(
            category="experiment",
            event_type=PAUSED_EVENT if paused else RESUMED_EVENT,
            summary="Owner paused automatic daily snapshots" if paused
            else "Owner resumed automatic daily snapshots",
            outcome="paused" if paused else "resumed",
            layer_id="SPS-09",
            payload={"source": "owner"},
        )
        return True

    def status(self) -> dict[str, Any]:
        latest = self._latest_pause_event()
        paused = latest is not None and latest["event_type"] == PAUSED_EVENT
        return {
            "enabled": self.enabled,
            "paused": paused,
            "paused_at": latest["occurred_at"] if paused else None,
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
        paused = self.paused
        running = [item for item in experiments if item["status"] == "running"]
        if not running and not paused:
            self._record_noop_day()
        for experiment in running:
            experiment_id = experiment["experiment_id"]
            due = self.observatory.get_snapshot_status(experiment_id)
            if due is None or not due["due"]:
                continue
            if paused:
                self._record_skipped_paused(experiment_id, due["day_index"])
                outcomes.append({"experiment_id": experiment_id, "status": "paused",
                                 "day_index": due["day_index"]})
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
                self.observatory.record_event(
                    category="experiment",
                    event_type=TELEMETRY_DEFERRED_EVENT,
                    summary=f"Daily capture deferred on Day {due['day_index']}: resource telemetry unavailable",
                    outcome="deferred",
                    day_index=due["day_index"],
                    experiment_id=experiment_id,
                    layer_id="SPS-09",
                    payload={"error_type": type(exc).__name__},
                )
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

    def _record_skipped_paused(self, experiment_id: str, day_index: int) -> None:
        """One explicit record per experiment-day, however often the loop checks."""
        if self.observatory.find_events(
            (SKIPPED_PAUSED_EVENT,), experiment_id=experiment_id, day_index=day_index, limit=1
        ):
            return
        self.observatory.record_event(
            category="experiment",
            event_type=SKIPPED_PAUSED_EVENT,
            summary=f"Day {day_index} snapshot not captured: automatic capture is paused",
            outcome="skipped",
            day_index=day_index,
            experiment_id=experiment_id,
            layer_id="SPS-09",
            payload={"reason": "owner_paused"},
        )

    def _record_noop_day(self) -> None:
        """One explicit no-op record per UTC day when the scheduler ran with nothing to capture."""
        today = datetime.now(UTC).date().isoformat()
        if self._noop_day == today:
            return
        if not self.observatory.find_events((NOOP_DAY_EVENT,), since=today, limit=1):
            self.observatory.record_event(
                category="experiment",
                event_type=NOOP_DAY_EVENT,
                summary="Scheduler active; no running experiment, so nothing to capture today",
                outcome="no_op",
                layer_id="SPS-09",
                payload={"date_utc": today, "running_experiments": 0},
            )
        self._noop_day = today

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
