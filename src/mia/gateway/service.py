"""Policy-controlled model selection and safe text-completion fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .contracts import (
    AuthenticationProviderError,
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    ProviderResponse,
    RoutePolicy,
    StreamChunk,
    TokenUsage,
    TransientProviderError,
)
from .health import CandidateHealthTracker
from .model_registry import ModelRegistry
from .registry import ProviderRegistry
from .resilience import CircuitBreaker, ResilienceLog, RetryPolicy
from .routing import actual_cost_usd
from .selection import DecisionLog, RoutingPreferences, select


@dataclass(frozen=True, slots=True)
class CompletionResult:
    provider_id: str
    model_id: str
    content: str
    attempted_candidates: tuple[str, ...]
    tool_calls: tuple[dict[str, Any], ...] = ()
    data_local: bool = False
    usage: TokenUsage | None = None
    cost_usd: float | None = 0.0
    free_tier: bool = True
    latency_ms: float | None = None  # wall time of the successful provider call


@dataclass(frozen=True, slots=True)
class ConnectionTestResult:
    """Sanitized outcome of a provider connection test (no URLs, headers or bodies)."""

    provider_id: str
    ok: bool
    status: str  # ok | unreachable | auth_failed | invalid_response | unknown_provider | error
    model_count: int = 0
    latency_ms: float | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """Gateway-level stream item. `done` is True on the final event only."""

    delta: str = ""
    done: bool = False
    provider_id: str = ""
    model_id: str = ""
    attempted_candidates: tuple[str, ...] = ()
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    cost_usd: float | None = None
    data_local: bool = False
    free_tier: bool = True
    first_token_ms: float | None = None  # request start to first text; final event only
    total_ms: float | None = None


def _brief(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:160]


def _aggregate_failure(attempted: list[str], last_error: Exception) -> TransientProviderError:
    """Summarise an exhausted fallback chain, keeping the last throttle's retry hint."""
    throttled = isinstance(last_error, TransientProviderError) and last_error.rate_limited
    return TransientProviderError(
        f"All eligible candidates failed. Tried: {', '.join(attempted)}",
        retry_after=last_error.retry_after if throttled else None,  # type: ignore[union-attr]
        rate_limited=throttled,
    )


class ModelGateway:
    def __init__(
        self,
        providers: ProviderRegistry,
        health: CandidateHealthTracker | None = None,
        model_registry: ModelRegistry | None = None,
        preferences: RoutingPreferences | None = None,
        decisions: DecisionLog | None = None,
        retry_policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        events: ResilienceLog | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.providers = providers
        self.health = health or CandidateHealthTracker()
        self.model_registry = model_registry
        self.preferences = preferences or RoutingPreferences()
        self.decisions = decisions or DecisionLog()
        self.retry_policy = retry_policy or RetryPolicy()
        self.events = events or ResilienceLog()
        self.breaker = breaker or CircuitBreaker(on_event=self.events.add)
        self._sleep = sleep

    async def _call_with_retry(self, candidate_name: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Run one provider call, retrying transient failures per the retry policy.

        Rate limits and authentication/permanent errors are never retried here. The
        retried operation is a pure generation request: nothing has been delivered
        to the caller or executed (tool calls run outside the gateway), so a retry
        cannot duplicate side effects.
        """
        attempts = self.retry_policy.max_attempts
        for attempt in range(attempts):
            try:
                return await call()
            except TransientProviderError as exc:
                if exc.rate_limited or attempt + 1 >= attempts:
                    raise
                delay = self.retry_policy.delay(attempt)
                self.events.add(
                    "retry", candidate_name, f"attempt {attempt + 1} failed; retry in {delay:.1f}s"
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def set_preferences(self, preferences: RoutingPreferences) -> None:
        """Replace routing preferences after validating them."""
        pinned = preferences.pinned
        if pinned is not None and (pinned.count("/") < 1 or len(pinned) > 300):
            raise ValueError("pinned model must be 'provider/model'")
        for key, value in preferences.preferences.items():
            if isinstance(value, bool) or not isinstance(value, int) or not -10 <= value <= 10:
                raise ValueError(f"preference for {key!r} must be an integer from -10 to 10")
        self.preferences = preferences

    def _rank(
        self,
        candidates: list[ModelCandidate],
        request: ModelRequest,
        policy: RoutePolicy,
        order_hint: list[ModelCandidate] | tuple[ModelCandidate, ...] | None,
    ) -> list[ModelCandidate]:
        ordered, decision = select(
            candidates, request, policy, self.preferences, hint=order_hint
        )
        self.decisions.add(decision)
        return ordered

    def _order_by_health(self, ranked: list[ModelCandidate]) -> list[ModelCandidate]:
        """Drop rate-limited candidates, then put degraded ones after healthy ones.

        The router's cost/reliability order is preserved within each group.
        Raises TransientProviderError (rate_limited) when every eligible
        candidate is inside a provider-advised wait, so callers can say when to retry.
        """
        if not ranked:
            return ranked
        usable = [c for c in ranked if not self.health.is_blocked(c)]
        if not usable:
            wait = min(self.health.blocked_for(c) for c in ranked)
            raise TransientProviderError(
                f"All eligible models are rate limited; retry in about {int(wait) + 1}s",
                retry_after=wait,
                rate_limited=True,
            )
        healthy = [c for c in usable if not self.health.is_degraded(c)]
        degraded = [c for c in usable if self.health.is_degraded(c)]
        return healthy + degraded

    @staticmethod
    def _apply_order_hint(
        ranked: list[ModelCandidate],
        hint: list[ModelCandidate] | tuple[ModelCandidate, ...] | None,
    ) -> list[ModelCandidate]:
        """Restrict and reorder the policy-eligible list to a caller's task-aware order.

        The hint can only narrow or reorder: a candidate the cost/quota policy
        rejected is never reintroduced, and candidates absent from the hint
        (e.g. judged unsuitable for the task) are dropped.
        """
        if hint is None:
            return ranked
        eligible = {(c.provider_id, c.model_id): c for c in ranked}
        return [
            eligible[(c.provider_id, c.model_id)]
            for c in hint
            if (c.provider_id, c.model_id) in eligible
        ]

    async def discover_models(self) -> list[ModelCandidate]:
        """Discover from all usable providers; one offline provider must not block others."""
        candidates: list[ModelCandidate] = []
        listed: list[str] = []
        for provider_id in self.providers.ids():
            provider = self.providers.get(provider_id)
            try:
                candidates.extend(await provider.list_models())
                listed.append(provider_id)
            except (TransientProviderError, PermanentProviderError):
                # An offline or misconfigured provider must not block the others.
                continue
        if self.model_registry is None:
            return candidates
        # Only providers that answered may mark their absent models stale.
        self.model_registry.sync_discovered(candidates, listed)
        return self.model_registry.apply(
            candidates, registered_providers=self.providers.ids()
        )

    async def test_connection(self, provider_id: str) -> ConnectionTestResult:
        """List models from one provider and classify the outcome safely."""
        try:
            provider = self.providers.get(provider_id)
        except KeyError:
            return ConnectionTestResult(provider_id, False, "unknown_provider")
        started = time.perf_counter()
        try:
            models = await provider.list_models()
        except AuthenticationProviderError:
            return ConnectionTestResult(
                provider_id, False, "auth_failed", detail="credentials were rejected"
            )
        except TransientProviderError as exc:
            return ConnectionTestResult(
                provider_id,
                False,
                "unreachable",
                detail="rate limited" if exc.rate_limited else "unavailable or timed out",
            )
        except PermanentProviderError:
            return ConnectionTestResult(
                provider_id, False, "invalid_response", detail="provider response was rejected"
            )
        except Exception:  # noqa: BLE001 - diagnostics must never raise or leak details
            return ConnectionTestResult(provider_id, False, "error", detail="unexpected error")
        elapsed = (time.perf_counter() - started) * 1000.0
        return ConnectionTestResult(
            provider_id, True, "ok", model_count=len(models), latency_ms=elapsed
        )

    async def complete(
        self,
        request: ModelRequest,
        candidates: list[ModelCandidate],
        policy: RoutePolicy = RoutePolicy(),
        order_hint: list[ModelCandidate] | tuple[ModelCandidate, ...] | None = None,
    ) -> CompletionResult:
        ranked = self._order_by_health(
            self._rank(candidates, request, policy, order_hint)
        )
        attempted: list[str] = []
        last_error: Exception | None = None
        skipped: list[str] = []

        for candidate in ranked:
            provider_id = candidate.provider_id
            candidate_name = f"{provider_id}/{candidate.model_id}"
            if not self.breaker.allow(provider_id):
                if provider_id not in skipped:
                    skipped.append(provider_id)
                    self.events.add("skipped", provider_id, "circuit open; provider paused")
                continue
            attempted.append(candidate_name)
            provider = self.providers.get(provider_id)
            settled = False
            try:
                started = time.perf_counter()
                response = await self._call_with_retry(
                    candidate_name, lambda: provider.complete(candidate, request)
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                if isinstance(response, str):
                    response = ProviderResponse(content=response)
                if not response.content and not response.tool_calls:
                    raise PermanentProviderError("Provider returned an empty response")
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                return CompletionResult(
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    content=response.content or "",
                    attempted_candidates=tuple(attempted),
                    tool_calls=response.tool_calls,
                    data_local=candidate.data_local,
                    usage=response.usage,
                    cost_usd=actual_cost_usd(candidate, response.usage, request),
                    free_tier=candidate.free_tier,
                    latency_ms=latency_ms,
                )
            except AuthenticationProviderError as exc:
                # Provider-specific: pause it and try another; never retried in a loop.
                self.breaker.record_auth_failure(provider_id)
                settled = True
                last_error = exc
                continue
            except TransientProviderError as exc:
                self.health.record_failure(candidate, exc)
                self.breaker.record_failure(provider_id, rate_limited=exc.rate_limited)
                self.events.add("failure", candidate_name, _brief(exc))
                settled = True
                last_error = exc
                continue
            except PermanentProviderError:
                raise
            finally:
                if not settled:
                    self.breaker.release(provider_id)

        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
        if skipped:
            raise TransientProviderError(
                "All eligible providers are temporarily paused: " + ", ".join(skipped)
            )
        raise RuntimeError("No eligible model is available under the current routing policy")

    async def stream(
        self,
        request: ModelRequest,
        candidates: list[ModelCandidate],
        policy: RoutePolicy = RoutePolicy(),
        order_hint: list[ModelCandidate] | tuple[ModelCandidate, ...] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a completion with the same routing policy as complete().

        Fallback to the next candidate is only safe before any text has been
        emitted; a failure after the first delta is raised to the caller so
        partial output is never silently mixed across models. Providers without
        a stream() method, and tool-bearing requests, use one buffered call.
        """
        ranked = self._order_by_health(
            self._rank(candidates, request, policy, order_hint)
        )
        attempted: list[str] = []
        last_error: Exception | None = None
        skipped: list[str] = []

        for candidate in ranked:
            provider_id = candidate.provider_id
            candidate_name = f"{provider_id}/{candidate.model_id}"
            if not self.breaker.allow(provider_id):
                if provider_id not in skipped:
                    skipped.append(provider_id)
                    self.events.add("skipped", provider_id, "circuit open; provider paused")
                continue
            attempted.append(candidate_name)
            provider = self.providers.get(provider_id)
            settled = False
            emitted = False
            streamer = getattr(provider, "stream", None)
            started = time.perf_counter()
            first_token_at: float | None = None
            last_chunk_at = started
            # Time this generator spends suspended at a yield belongs to the
            # consumer, not the provider; it is excluded from the measurements.
            paused = 0.0
            paused_at_last_chunk = 0.0
            try:
                if streamer is None or request.tools:
                    response = await provider.complete(candidate, request)
                    last_chunk_at = first_token_at = time.perf_counter()
                    if isinstance(response, str):
                        response = ProviderResponse(content=response)
                    if not response.content:
                        raise PermanentProviderError("Provider returned an empty response")
                    chunks: list[StreamChunk] = [
                        StreamChunk(
                            delta=response.content,
                            finish_reason=response.finish_reason or "stop",
                            usage=response.usage,
                        )
                    ]
                    for chunk in chunks:
                        emitted = True
                        yielded_at = time.perf_counter()
                        yield StreamEvent(delta=chunk.delta)
                        paused += time.perf_counter() - yielded_at
                    final = chunks[-1]
                else:
                    final = StreamChunk()
                    text_parts: list[str] = []
                    async for chunk in streamer(candidate, request):
                        # Timestamps are taken on receipt, before yielding to the
                        # consumer, so a slow consumer does not inflate latency.
                        last_chunk_at = time.perf_counter()
                        paused_at_last_chunk = paused
                        if chunk.delta:
                            if first_token_at is None:
                                first_token_at = last_chunk_at
                            emitted = True
                            text_parts.append(chunk.delta)
                            yielded_at = time.perf_counter()
                            yield StreamEvent(delta=chunk.delta)
                            paused += time.perf_counter() - yielded_at
                        if chunk.finish_reason or chunk.usage:
                            final = chunk
                    if not text_parts:
                        raise PermanentProviderError("Provider returned an empty response")
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                yield StreamEvent(
                    done=True,
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    attempted_candidates=tuple(attempted),
                    finish_reason=final.finish_reason or "stop",
                    usage=final.usage,
                    cost_usd=actual_cost_usd(candidate, final.usage, request),
                    data_local=candidate.data_local,
                    free_tier=candidate.free_tier,
                    first_token_ms=(
                        (first_token_at - started) * 1000.0 if first_token_at else None
                    ),
                    total_ms=max(0.0, last_chunk_at - started - paused_at_last_chunk) * 1000.0,
                )
                return
            except AuthenticationProviderError as exc:
                self.breaker.record_auth_failure(provider_id)
                settled = True
                if emitted:
                    raise
                last_error = exc
                continue
            except TransientProviderError as exc:
                self.health.record_failure(candidate, exc)
                self.breaker.record_failure(provider_id, rate_limited=exc.rate_limited)
                self.events.add("failure", candidate_name, _brief(exc))
                settled = True
                if emitted:
                    raise
                last_error = exc
                continue
            finally:
                if not settled:
                    self.breaker.release(provider_id)

        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
        if skipped:
            raise TransientProviderError(
                "All eligible providers are temporarily paused: " + ", ".join(skipped)
            )
        raise RuntimeError("No eligible model is available under the current routing policy")
