"""Read-only activity evidence for the daily experiment snapshots.

Every number comes from a store's own recorded events for the window. A source
that cannot be read is reported as unavailable; it is never replaced by zeros,
so a failure cannot be mistaken for a quiet day.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Counts(Protocol):
    def event_counts(self, since: str, until: str) -> dict[str, Any]: ...


class _Usage(Protocol):
    def window_summary(self, since: str, until: str) -> dict[str, Any]: ...


class ExperimentActivityCollector:
    def __init__(
        self,
        *,
        usage_ledger: _Usage | None = None,
        capability_store: _Counts | None = None,
        task_store: _Counts | None = None,
    ) -> None:
        self.sources: dict[str, Any] = {
            "inference_usage": (usage_ledger, "window_summary"),
            "capabilities": (capability_store, "event_counts"),
            "tasks": (task_store, "event_counts"),
        }

    def collect(self, since: str, until: str) -> dict[str, Any]:
        activity: dict[str, Any] = {
            "window": {"since": since, "until": until},
            "attribution": (
                "Counts of each store's own events recorded inside this window. The stores "
                "are not linked to experiment IDs, so these are window counts, not proof "
                "that an event was caused by the experiment."
            ),
        }
        for name, (source, method) in self.sources.items():
            if source is None:
                activity[name] = {"available": False, "reason": "source not configured"}
                continue
            try:
                activity[name] = {"available": True, **getattr(source, method)(since, until)}
            except Exception as exc:  # evidence gap must be visible, not fatal
                activity[name] = {"available": False, "reason": type(exc).__name__}
        return activity
