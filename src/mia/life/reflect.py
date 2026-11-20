"""Deterministic reflection: a digest of what really happened since the last one.

Observations are counts and facts read from the journal; each reflection lists the
cycle ids it reviewed. No model is called, so nothing here can be invented.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from mia.life.store import LifeStore

REFLECT_EVERY = 5


class Reflector:
    def __init__(self, store: LifeStore, *, every: int = REFLECT_EVERY) -> None:
        if every < 1:
            raise ValueError("every must be at least 1")
        self.store = store
        self.every = every

    def reflect(self, now: datetime) -> str | None:
        """Write a reflection once `every` new real cycles have finished; else return None."""
        last = self.store.list_reflections(limit=1)
        since = datetime.fromisoformat(last[0]["window_end"]) if last else None
        cycles = [c for c in self.store.finished_cycles_since(since) if c["outcome"] != "no_op"]
        if len(cycles) < self.every:
            return None
        outcomes = Counter(c["outcome"] for c in cycles)
        hosts = sorted({s["host"] for c in cycles for s in c["sources"] if s.get("host")})
        decisions = Counter(c["notification_decision"].split(":")[0] for c in cycles)
        observations = [
            f"Reviewed {len(cycles)} cycles: "
            + ", ".join(f"{n} {name}" for name, n in sorted(outcomes.items())) + ".",
        ]
        if hosts:
            observations.append("External hosts read: " + ", ".join(hosts) + ".")
        failures = [c for c in cycles if c["outcome"] == "failed"]
        if failures:
            errors = sorted({c["error"] for c in failures if c["error"]})
            observations.append(
                f"{len(failures)} cycle(s) failed" + (f" ({', '.join(errors)})" if errors else "") + "."
            )
        observations.append(
            "Owner messages: "
            + f"{decisions.get('sent', 0)} sent, {decisions.get('suppressed', 0)} suppressed, "
            + f"{decisions.get('none', 0)} not warranted."
        )
        window_end = max(datetime.fromisoformat(c["ended_at"]) for c in cycles)
        return self.store.add_reflection(
            window_start=since, window_end=window_end, cycles_reviewed=len(cycles),
            observations=observations, evidence=[c["cycle_id"] for c in cycles],
        )
