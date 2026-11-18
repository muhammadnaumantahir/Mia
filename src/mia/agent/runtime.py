"""Explicitly started, resource-aware autonomy scheduler.

The runtime never enables itself. Callers must provide a governed executor and
an enabled policy; every cycle samples local resources before claiming a task.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from .resources import ResourceMonitor
from .tasks import AutonomyPolicy, RuntimeLoad, TaskCoordinator, TaskStore


class AutonomyRuntime:
    """A single-worker scheduler with pause/resume and an immediate stop signal."""

    def __init__(self, *, tasks: TaskStore, monitor: ResourceMonitor,
                 policy: AutonomyPolicy,
                 executor: Callable[[dict[str, Any]], dict[str, Any] | None],
                 interval_seconds: float = 30.0,
                 on_cycle: Callable[[dict[str, Any]], None] | None = None) -> None:
        if not 1.0 <= interval_seconds <= 3600.0:
            raise ValueError("interval_seconds must be between 1 and 3600")
        self.tasks = tasks
        self.monitor = monitor
        self.policy = policy
        self.executor = executor
        self.interval_seconds = interval_seconds
        self.on_cycle = on_cycle
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cycle_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._last_result: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self) -> dict[str, Any] | None:
        with self._state_lock:
            return self._last_result

    def update_policy(self, policy: AutonomyPolicy) -> None:
        """Pause future work immediately when disabled or emergency-stopped."""
        with self._state_lock:
            self.policy = policy
            if not policy.enabled or policy.emergency_stop:
                self._stop.set()

    def run_cycle(self) -> dict[str, Any]:
        """Run one synchronous cycle; useful for tests and manual operation."""
        if not self._cycle_lock.acquire(blocking=False):
            return {"status": "busy", "reason": "cycle_already_running"}
        try:
            with self._state_lock:
                policy = self.policy
            if not policy.enabled:
                result = {"status": "paused", "reason": "autonomy_disabled"}
            elif policy.emergency_stop:
                result = {"status": "paused", "reason": "emergency_stop"}
            else:
                try:
                    snapshot = self.monitor.snapshot()
                    load = RuntimeLoad(cpu_percent=float(snapshot["cpu_percent"]),
                                       memory_percent=float(snapshot["memory_percent"]))
                    result = TaskCoordinator(self.tasks, policy).dispatch_one(
                        load=load, executor=self.executor)
                except (RuntimeError, ValueError, KeyError, TypeError) as exc:
                    result = {"status": "paused", "reason": "resource_or_policy_error",
                              "error_type": type(exc).__name__}
            with self._state_lock:
                self._last_result = result
            if self.on_cycle is not None:
                try:
                    self.on_cycle(result)
                except Exception as exc:
                    # Observability must never terminate task scheduling.
                    result = {
                        **result,
                        "observability_error": type(exc).__name__,
                    }
                    with self._state_lock:
                        self._last_result = result
            return result
        finally:
            self._cycle_lock.release()

    def start(self) -> None:
        """Start the background loop only after explicit owner configuration."""
        with self._state_lock:
            if not self.policy.enabled:
                raise RuntimeError("autonomy must be explicitly enabled before start")
            if self.policy.emergency_stop:
                raise RuntimeError("emergency stop is active")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="mia-autonomy", daemon=True)
            self._thread.start()

    def pause(self) -> None:
        """Stop future cycles; an in-flight bounded executor call is not killed."""
        self._stop.set()

    def resume(self) -> None:
        """Resume only if the current policy still permits autonomy."""
        self.start()

    def shutdown(self, timeout: float = 5.0) -> bool:
        self.pause()
        with self._state_lock:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.run_cycle()
            if self._stop.wait(self.interval_seconds):
                break
