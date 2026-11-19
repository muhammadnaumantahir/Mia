"""In-memory candidate health: rate-limit blocking and failure backoff.

Policy (see docs/architecture/AI_GATEWAY.md):

* A provider throttle (HTTP 429, or 503 with Retry-After) *blocks* the
  candidate until the advised wait elapses. Retrying earlier only prolongs the
  limit and risks provider-term violations.
* Any other transient failure *degrades* the candidate: it is tried after
  healthy candidates, with exponential backoff, but is never excluded, so a
  single local model stays usable after a blip.
* A success clears the record. Permanent errors are request-specific and are
  not recorded here.

State is process-local and intentionally not persisted: cooldowns are short
and a restart is a legitimate reset. Nothing here makes network calls.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .contracts import ModelCandidate, TransientProviderError

_MAX_TRACKED = 512


@dataclass(slots=True)
class _Record:
    consecutive_failures: int = 0
    blocked_until: float = 0.0
    degraded_until: float = 0.0
    rate_limited: bool = False
    last_error: str = ""


def candidate_key(candidate: ModelCandidate) -> str:
    return f"{candidate.provider_id}/{candidate.model_id}"


class CandidateHealthTracker:
    def __init__(
        self,
        *,
        base_backoff_seconds: float = 5.0,
        max_backoff_seconds: float = 300.0,
        max_retry_after_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("base_backoff_seconds", base_backoff_seconds),
            ("max_backoff_seconds", max_backoff_seconds),
            ("max_retry_after_seconds", max_retry_after_seconds),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite, non-negative number")
        self._base = base_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._max_retry_after = max_retry_after_seconds
        self._clock = clock
        self._records: dict[str, _Record] = {}
        self._lock = threading.Lock()

    def record_success(self, candidate: ModelCandidate) -> None:
        with self._lock:
            self._records.pop(candidate_key(candidate), None)

    def record_failure(self, candidate: ModelCandidate, error: TransientProviderError) -> None:
        now = self._clock()
        key = candidate_key(candidate)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                self._evict_if_full(now)
                record = self._records[key] = _Record()
            record.consecutive_failures += 1
            record.last_error = str(error)[:200]
            exponent = min(record.consecutive_failures - 1, 16)
            backoff = min(self._max_backoff, self._base * (2**exponent))
            if error.rate_limited:
                wait = error.retry_after if error.retry_after is not None else backoff
                record.rate_limited = True
                record.blocked_until = now + min(self._max_retry_after, wait)
            else:
                record.rate_limited = False
                record.degraded_until = now + backoff

    def is_blocked(self, candidate: ModelCandidate) -> bool:
        return self.blocked_for(candidate) > 0

    def blocked_for(self, candidate: ModelCandidate) -> float:
        """Seconds until a rate-limited candidate may be tried again (0 if usable)."""
        with self._lock:
            record = self._records.get(candidate_key(candidate))
            if record is None:
                return 0.0
            return max(0.0, record.blocked_until - self._clock())

    def is_degraded(self, candidate: ModelCandidate) -> bool:
        with self._lock:
            record = self._records.get(candidate_key(candidate))
            return record is not None and record.degraded_until > self._clock()

    def snapshot(self) -> list[dict[str, object]]:
        now = self._clock()
        with self._lock:
            items = []
            for key, record in sorted(self._records.items()):
                blocked = max(0.0, record.blocked_until - now)
                degraded = max(0.0, record.degraded_until - now)
                if blocked == 0 and degraded == 0:
                    state = "recovering"
                else:
                    state = "rate_limited" if blocked > 0 else "degraded"
                items.append(
                    {
                        "candidate": key,
                        "state": state,
                        "consecutive_failures": record.consecutive_failures,
                        "retry_in_seconds": round(max(blocked, degraded), 1),
                        "last_error": record.last_error,
                    }
                )
            return items

    def _evict_if_full(self, now: float) -> None:
        if len(self._records) < _MAX_TRACKED:
            return
        expired = [
            key
            for key, rec in self._records.items()
            if rec.blocked_until <= now and rec.degraded_until <= now
        ]
        for key in expired or [next(iter(self._records))]:
            self._records.pop(key, None)
