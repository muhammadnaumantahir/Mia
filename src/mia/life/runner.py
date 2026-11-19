"""One bounded, journaled autonomous cycle, plus the opt-in scheduler that drives it.

A cycle observes (derives follow-ups from real data), selects the most valuable
eligible curiosity item, explores it read-only, and records evidence. It never
writes memory, calls external services, runs code or contacts the owner; the only
explorer shipped here reviews Mia's own stored memory. When nothing can be done the
journal says so. Cycles that are gated off (paused, over budget, busy machine) are
returned to the caller but never journaled, so the journal only ever contains work
that actually started.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Protocol

from mia.life.store import LifeSettings, LifeStore, utc_now
from mia.memory.store import MemoryStore, extract_query_terms

NO_MODEL_RESOURCE = {"model_id": None, "provider_id": None, "model_calls": 0, "cost_usd": 0.0}
DEFER_HOURS = 24


@dataclass(frozen=True, slots=True)
class CycleContext:
    deadline: float  # time.monotonic() value; explorers must stop by then
    should_stop: Callable[[], bool]


@dataclass(frozen=True, slots=True)
class ExplorationResult:
    outcome: str  # "completed" or "deferred"
    item_state: str  # "done", "blocked" or "deferred"
    summary: str
    state_reason: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    resource: dict[str, Any] = field(default_factory=lambda: dict(NO_MODEL_RESOURCE))
    result: dict[str, Any] | None = None


class Explorer(Protocol):
    name: str
    permission_level: int  # 0 = internal, read-only

    def explore(self, item: dict[str, Any], context: CycleContext) -> ExplorationResult: ...


class MemoryReviewExplorer:
    """Level 0: checks what Mia's own memory already holds about an item. No network, no model."""

    name = "memory_review"
    permission_level = 0

    def __init__(self, memory: MemoryStore) -> None:
        self.memory = memory

    @staticmethod
    def _enough(terms: list[str], text: str) -> bool:
        lowered = text.lower()
        return sum(1 for term in terms if term in lowered) >= min(2, len(terms))

    def explore(self, item: dict[str, Any], context: CycleContext) -> ExplorationResult:
        query = f"{item['title']} {item['why_it_matters']}"
        terms = extract_query_terms(query)
        own_ref = item.get("source_ref", {}).get("ref")
        sources: list[dict[str, Any]] = []
        counts = {"knowledge": 0, "records": 0, "messages": 0}

        for entry in self.memory.retrieve_items(query, limit=8):
            if self._enough(terms, f"{entry['title']} {entry['content']}"):
                sources.append({"type": "knowledge", "ref": entry["item_id"], "title": entry["title"][:120]})
                counts["knowledge"] += 1
        if context.should_stop():
            return self._stopped()
        for entry in self.memory.retrieve_records(query, limit=8):
            if entry["record_id"] == own_ref:
                continue  # a goal follow-up must not "find" the goal it came from
            if self._enough(terms, f"{entry['title']} {entry['body']}"):
                sources.append({"type": "record", "ref": entry["record_id"], "title": entry["title"][:120]})
                counts["records"] += 1
        if context.should_stop():
            return self._stopped()
        for entry in self.memory.retrieve_messages(query, limit=8):
            if self._enough(terms, entry["content"]):
                sources.append({"type": "message", "ref": entry["message_id"],
                                "conversation_id": entry["conversation_id"]})
                counts["messages"] += 1

        actions = ["memory.search:knowledge", "memory.search:records", "memory.search:messages"]
        if sources:
            total = len(sources)
            return ExplorationResult(
                outcome="completed",
                item_state="done",
                summary=(
                    f"Reviewed Mia's own memory: {total} related entr{'y' if total == 1 else 'ies'} "
                    f"({counts['knowledge']} knowledge, {counts['records']} records, "
                    f"{counts['messages']} past messages). No external research was performed."
                ),
                sources=sources,
                actions=actions,
                result={"related": counts, "external_research": False},
            )
        return ExplorationResult(
            outcome="deferred",
            item_state="blocked",
            state_reason="no_enabled_research_source",
            summary=(
                "Nothing relevant in Mia's own memory and no external research source is "
                "enabled, so this item stays blocked until one is."
            ),
            actions=actions,
            result={"related": counts, "external_research": False},
        )

    @staticmethod
    def _stopped() -> ExplorationResult:
        return ExplorationResult(outcome="deferred", item_state="deferred", summary="Stopped early")


def derive_followups(
    store: LifeStore, memory: MemoryStore, settings: LifeSettings, now: datetime
) -> int:
    """Queue a follow-up for each active goal untouched for `stale_goal_days`. Returns new items."""
    created = 0
    cutoff = timedelta(days=settings.stale_goal_days)
    for goal in memory.structured.list(record_type="goal", status="active", limit=200):
        age = now - datetime.fromisoformat(goal["updated_at"])
        if age < cutoff:
            continue
        _, is_new = store.add_item(
            title=f"Review progress on goal: {goal['title']}"[:300],
            why_it_matters=f"Active goal has not been updated for {age.days} days.",
            origin="goal_followup",
            source_ref={"type": "record", "ref": goal["record_id"]},
            source_key=f"goal:{goal['record_id']}:{goal['updated_at']}",
            expected_value=0.6,
            novelty=0.3,
        )
        created += int(is_new)
    return created


def score_item(item: dict[str, Any], now: datetime) -> tuple[float, str]:
    age_days = max(0.0, (now - datetime.fromisoformat(item["created_at"])).total_seconds() / 86400)
    age_factor = min(age_days / 14.0, 1.0)
    attempts = int(item["attempts"])
    score = 0.55 * item["expected_value"] + 0.2 * item["novelty"] + 0.15 * age_factor - 0.15 * attempts
    rationale = (
        f"score {score:.2f} = 0.55*value {item['expected_value']:.2f} + 0.2*novelty "
        f"{item['novelty']:.2f} + 0.15*age {age_factor:.2f} - 0.15*attempts {attempts}"
    )
    return score, rationale


class LifeRunner:
    def __init__(
        self,
        store: LifeStore,
        memory: MemoryStore,
        explorer: Explorer | None = None,
        *,
        resource_snapshot: Callable[[], dict[str, Any]] | None = None,
        clock: Callable[[], datetime] = utc_now,
        on_cycle: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.store = store
        self.memory = memory
        self.explorer = explorer or MemoryReviewExplorer(memory)
        self.resource_snapshot = resource_snapshot
        self.clock = clock
        self.on_cycle = on_cycle
        self._lock = threading.Lock()
        self._state = threading.Lock()
        self._last_result: dict[str, Any] | None = None

    @property
    def last_result(self) -> dict[str, Any] | None:
        with self._state:
            return self._last_result

    def status(self) -> dict[str, Any]:
        now = self.clock()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        settings = self.store.get_settings()
        return {
            "settings": asdict(settings),
            "curiosity_counts": self.store.counts_by_state(),
            "cycles_today": self.store.cycles_started_since(day_start),
            "last_result": self.last_result,
            "explorer": {"name": self.explorer.name, "permission_level": self.explorer.permission_level},
            "external_research_enabled": False,
            "proactive_delivery_enabled": False,
        }

    # ------------------------------------------------------------------ cycle

    def _gate(self, trigger: str, settings: LifeSettings, now: datetime) -> dict[str, Any] | None:
        """Return a skip result when the cycle must not start; owner requests bypass all but a stop."""
        if settings.emergency_stop:
            return {"status": "skipped", "reason": "emergency_stop"}
        if trigger == "owner":
            return None
        if settings.paused:
            return {"status": "skipped", "reason": "paused"}
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if self.store.cycles_started_since(day_start) >= settings.max_cycles_per_day:
            return {"status": "skipped", "reason": "daily_cycle_cap"}
        last = self.store.last_cycle_started_at()
        if last and (now - last).total_seconds() < settings.min_interval_seconds:
            return {"status": "skipped", "reason": "min_interval"}
        if self.resource_snapshot is None:
            return {"status": "skipped", "reason": "resources_unavailable"}
        try:
            snapshot = self.resource_snapshot()
            cpu, memory = float(snapshot["cpu_percent"]), float(snapshot["memory_percent"])
        except (RuntimeError, KeyError, TypeError, ValueError):
            return {"status": "skipped", "reason": "resources_unavailable"}
        if not (0.0 <= cpu <= 100.0 and 0.0 <= memory <= 100.0):
            return {"status": "skipped", "reason": "resources_unavailable"}
        if cpu > settings.max_cpu_percent or memory > settings.max_memory_percent:
            return {"status": "skipped", "reason": "resources_busy"}
        return None

    def run_cycle(self, trigger: str = "schedule", detail: str = "") -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {"status": "skipped", "reason": "cycle_already_running"}
        try:
            result = self._run_cycle(trigger, detail)
        finally:
            self._lock.release()
        with self._state:
            self._last_result = {**result, "at": self.clock().isoformat()}
        if self.on_cycle is not None and result.get("status") != "skipped":
            try:
                self.on_cycle({**result, "trigger": trigger})
            except Exception:  # observability must never break scheduling
                pass
        return result

    def _run_cycle(self, trigger: str, detail: str) -> dict[str, Any]:
        now = self.clock()
        settings = self.store.get_settings()
        skipped = self._gate(trigger, settings, now)
        if skipped:
            return skipped
        policy = {"trigger": trigger, "gates_passed": True, **{
            k: getattr(settings, k) for k in ("max_cycles_per_day", "min_interval_seconds", "time_budget_seconds")
        }}

        created = derive_followups(self.store, self.memory, settings, now)
        eligible = self.store.eligible_items(max_attempts=settings.max_attempts, now=now)
        if not eligible:
            reason = "No eligible curiosity item; nothing to investigate."
            cycle_id = self.store.record_no_op(
                trigger=trigger, trigger_detail=detail or trigger, reason=reason, policy=policy
            )
            return {"status": "no_op", "cycle_id": cycle_id, "derived_followups": created}

        ranked = sorted(((*score_item(item, now), item) for item in eligible),
                        key=lambda entry: entry[0], reverse=True)
        _, rationale, item = ranked[0]
        cycle_id = self.store.start_cycle(
            trigger=trigger,
            trigger_detail=detail or trigger,
            goal=f"Investigate: {item['title']}"[:500],
            rationale=f"{rationale}; {len(eligible)} eligible; {created} follow-up(s) derived this cycle",
            item_id=item["item_id"],
            policy=policy,
            permission_level=self.explorer.permission_level,
        )
        self.store.set_item_state(item["item_id"], "in_progress", reason="cycle started")
        started = monotonic()
        budget = settings.time_budget_seconds

        def stop_requested() -> bool:
            live = self.store.get_settings()
            return live.emergency_stop or (live.paused and trigger != "owner")

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mia-life")
        try:
            future = pool.submit(
                self.explorer.explore, item, CycleContext(started + budget, stop_requested)
            )
            try:
                outcome = future.result(timeout=budget)
                error = None
            except FutureTimeout:
                outcome, error = None, "time_budget_exceeded"
            except Exception as exc:  # explorer faults are evidence, not crashes
                outcome, error = None, type(exc).__name__
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        duration_ms = int((monotonic() - started) * 1000)

        if error:
            self.store.finish_cycle(
                cycle_id, outcome="failed", summary=f"Exploration failed: {error}",
                actions=[], resource=dict(NO_MODEL_RESOURCE), duration_ms=duration_ms, error=error,
                next_follow_up="Item deferred and its attempt counted",
            )
            self.store.set_item_state(
                item["item_id"], "deferred", reason=error,
                defer_until=self.clock() + timedelta(hours=1), count_attempt=True,
            )
            return {"status": "failed", "cycle_id": cycle_id, "item_id": item["item_id"], "reason": error}

        assert outcome is not None
        if stop_requested():  # an owner stop arrived mid-cycle: discard, do not record findings
            self.store.finish_cycle(
                cycle_id, outcome="cancelled",
                summary="Stopped by an owner control before results were recorded",
                actions=outcome.actions, duration_ms=duration_ms,
            )
            self.store.set_item_state(item["item_id"], "queued", reason="cycle cancelled by owner stop")
            return {"status": "cancelled", "cycle_id": cycle_id, "item_id": item["item_id"]}

        self.store.finish_cycle(
            cycle_id,
            outcome=outcome.outcome,
            summary=outcome.summary,
            sources=outcome.sources,
            actions=outcome.actions,
            resource=outcome.resource,
            duration_ms=duration_ms,
            next_follow_up=None if outcome.item_state == "done" else "Item kept for the owner to review",
        )
        wait = self.clock() + timedelta(hours=DEFER_HOURS) if outcome.item_state == "deferred" else None
        self.store.set_item_state(
            item["item_id"], outcome.item_state, reason=outcome.state_reason or outcome.outcome,
            defer_until=wait, result=outcome.result, count_attempt=outcome.item_state != "done",
        )
        return {"status": outcome.outcome, "cycle_id": cycle_id, "item_id": item["item_id"]}


class LifeScheduler:
    """Opt-in background loop: wakes on a timer or an event, runs one cycle, sleeps."""

    def __init__(self, runner: LifeRunner, *, interval_seconds: float = 300.0) -> None:
        if not 5.0 <= interval_seconds <= 86_400.0:
            raise ValueError("interval_seconds must be between 5 and 86400")
        self.runner = runner
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()
        self._pending: tuple[str, str] | None = None

    @property
    def running(self) -> bool:
        with self._guard:
            return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        with self._guard:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="mia-life-scheduler", daemon=True)
            self._thread.start()

    def wake(self, detail: str = "") -> None:
        """Request an early cycle because something happened (an event trigger)."""
        with self._guard:
            self._pending = ("event", detail)
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        self._wake.set()
        with self._guard:
            thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=max(0.0, timeout))
        return not thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=self.interval_seconds)
            if self._stop.is_set():
                break
            with self._guard:
                pending, self._pending = self._pending, None
            self._wake.clear()
            trigger, detail = pending or ("schedule", "interval elapsed")
            try:
                self.runner.run_cycle(trigger, detail)
            except Exception:  # never let the loop die silently; status shows last_result
                with self.runner._state:
                    self.runner._last_result = {"status": "error", "reason": "unexpected_runtime_error"}
