"""Retry policy, provider circuit breaker and a bounded resilience event log.

Scope and rules:

* Retries apply to *transient* failures only (connection errors, timeouts, 5xx),
  never to rate limits (those block the candidate until the provider's advised
  wait) and never to authentication failures or request-specific errors.
* The breaker works per provider. After repeated transient failures it opens and
  the provider is skipped until a cooldown elapses; then exactly one probe request
  is allowed (half-open). A failed probe re-opens with a longer cooldown, a
  successful one closes the circuit. An authentication failure opens it at once
  for a long cooldown and is never retried in a loop.
* Event details are short, sanitized strings: no URLs, headers, bodies or prompts.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1  # total tries per candidate; 1 means no same-candidate retry
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter: float = 0.2  # fraction of the delay, randomised either way

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be an integer from 1 to 5")
        for name in ("base_delay_seconds", "max_delay_seconds", "jitter"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative number")
        if self.jitter > 1 or self.max_delay_seconds > 60:
            raise ValueError("jitter must be <= 1 and max_delay_seconds <= 60")

    def delay(self, attempt_index: int, rng: Callable[[], float] = random.random) -> float:
        """Exponential backoff with jitter for the retry after attempt ``attempt_index`` (0-based)."""
        base = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** min(attempt_index, 16)))
        spread = base * self.jitter * (2 * rng() - 1)
        return max(0.0, min(self.max_delay_seconds, base + spread))


@dataclass(frozen=True, slots=True)
class ResilienceEvent:
    kind: str  # retry | failure | circuit_open | circuit_half_open | circuit_closed | auth_failure | skipped
    subject: str  # provider id or provider/model
    detail: str
    at: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "subject": self.subject, "detail": self.detail, "at": self.at}


class ResilienceLog:
    def __init__(self, capacity: int = 200) -> None:
        self._items: deque[ResilienceEvent] = deque(maxlen=max(1, capacity))
        self._lock = threading.Lock()

    def add(self, kind: str, subject: str, detail: str = "") -> None:
        event = ResilienceEvent(kind, subject[:200], detail[:200], datetime.now(UTC).isoformat())
        with self._lock:
            self._items.append(event)

    def recent(self, limit: int = 50) -> list[ResilienceEvent]:
        with self._lock:
            return list(self._items)[-max(0, limit):][::-1]


@dataclass(slots=True)
class _Circuit:
    consecutive_failures: int = 0
    opened_until: float = 0.0
    open_count: int = 0
    probing: bool = False
    reason: str = ""
    ever_opened: bool = field(default=False)


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        open_seconds: float = 30.0,
        max_open_seconds: float = 300.0,
        auth_open_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        on_event: Callable[[str, str, str], None] | None = None,
    ) -> None:
        if isinstance(failure_threshold, bool) or not 1 <= failure_threshold <= 100:
            raise ValueError("failure_threshold must be an integer from 1 to 100")
        for name, value in (
            ("open_seconds", open_seconds),
            ("max_open_seconds", max_open_seconds),
            ("auth_open_seconds", auth_open_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite, positive number")
        self._threshold = failure_threshold
        self._open = open_seconds
        self._max_open = max_open_seconds
        self._auth_open = auth_open_seconds
        self._clock = clock
        self._on_event = on_event
        self._circuits: dict[str, _Circuit] = {}
        self._lock = threading.Lock()

    def _emit(self, kind: str, provider_id: str, detail: str = "") -> None:
        if self._on_event is not None:
            self._on_event(kind, provider_id, detail)

    def allow(self, provider_id: str) -> bool:
        """True if a request may be sent now. In half-open state only one probe is allowed."""
        with self._lock:
            circuit = self._circuits.get(provider_id)
            if circuit is None or not circuit.ever_opened:
                return True
            if self._clock() < circuit.opened_until:
                return False
            if circuit.probing:
                return False
            circuit.probing = True
        self._emit("circuit_half_open", provider_id, "probe request allowed")
        return True

    def release(self, provider_id: str) -> None:
        """Free the probe slot when a request ended without a countable outcome."""
        with self._lock:
            circuit = self._circuits.get(provider_id)
            if circuit is not None:
                circuit.probing = False

    def record_success(self, provider_id: str) -> None:
        with self._lock:
            circuit = self._circuits.pop(provider_id, None)
        if circuit is not None and circuit.ever_opened:
            self._emit("circuit_closed", provider_id, "provider recovered")

    def record_failure(self, provider_id: str, *, rate_limited: bool = False) -> None:
        """Count a transient failure. Throttling does not count: the provider is up."""
        if rate_limited:
            self.release(provider_id)
            return
        opened = False
        with self._lock:
            circuit = self._circuits.setdefault(provider_id, _Circuit())
            if circuit.probing:
                circuit.probing = False
                circuit.open_count += 1
                self._open_circuit(circuit, "probe failed")
                opened = True
            else:
                circuit.consecutive_failures += 1
                if circuit.consecutive_failures >= self._threshold and not self._is_open(circuit):
                    circuit.open_count += 1
                    self._open_circuit(circuit, "repeated failures")
                    opened = True
            reason = circuit.reason
            seconds = max(0.0, circuit.opened_until - self._clock())
        if opened:
            self._emit("circuit_open", provider_id, f"{reason}; cooling down {int(seconds)}s")

    def record_auth_failure(self, provider_id: str) -> None:
        with self._lock:
            circuit = self._circuits.setdefault(provider_id, _Circuit())
            circuit.probing = False
            circuit.open_count += 1
            circuit.ever_opened = True
            circuit.reason = "credentials rejected"
            circuit.opened_until = self._clock() + self._auth_open
        self._emit("auth_failure", provider_id, "credentials rejected; provider paused")

    def _is_open(self, circuit: _Circuit) -> bool:
        return circuit.ever_opened and self._clock() < circuit.opened_until

    def _open_circuit(self, circuit: _Circuit, reason: str) -> None:
        duration = min(self._max_open, self._open * (2 ** min(circuit.open_count - 1, 16)))
        circuit.ever_opened = True
        circuit.reason = reason
        circuit.opened_until = self._clock() + duration

    def state(self, provider_id: str) -> str:
        with self._lock:
            circuit = self._circuits.get(provider_id)
            if circuit is None or not circuit.ever_opened:
                return "closed"
            if self._clock() < circuit.opened_until:
                return "open"
            return "half_open"

    def snapshot(self) -> list[dict[str, object]]:
        now = self._clock()
        with self._lock:
            return [
                {
                    "provider": provider_id,
                    "state": (
                        "closed"
                        if not c.ever_opened
                        else "open" if now < c.opened_until else "half_open"
                    ),
                    "consecutive_failures": c.consecutive_failures,
                    "retry_in_seconds": round(max(0.0, c.opened_until - now), 1),
                    "reason": c.reason,
                }
                for provider_id, c in sorted(self._circuits.items())
            ]
