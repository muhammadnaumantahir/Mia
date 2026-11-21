"""Policy-controlled model selection and safe text-completion fallback."""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from .contracts import (
    AuthenticationProviderError,
    EmptyResponseError,
    ModelCandidate,
    ModelRequest,
    ModelUnavailableError,
    PermanentProviderError,
    ProviderResponse,
    RoutePolicy,
    StreamChunk,
    StreamInterruptedError,
    TokenUsage,
    TransientProviderError,
)
from .health import CandidateHealthTracker
from .model_registry import ModelRegistry
from .registry import ProviderRegistry
from .resilience import CircuitBreaker, ResilienceLog, RetryPolicy
from .routing import actual_cost_usd, estimate_cost_usd
from .selection import DecisionLog, RoutingPreferences, select
from .toolcalls import parse_structured, screen_tool_calls


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
    structured: Any = None  # parsed JSON when the request had a json_schema
    tool_call_issues: tuple[dict[str, object], ...] = ()  # sanitized screening findings
    reservation_id: str | None = None


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
    tool_calls: tuple[dict[str, Any], ...] = ()  # whole, normalized calls; final event only
    tool_call_issues: tuple[dict[str, object], ...] = ()
    reservation_id: str | None = None


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


DEFAULT_ATTEMPT_TIMEOUT_S = 180.0  # backstop above the adapters' own 90s HTTP timeouts
DEFAULT_STREAM_IDLE_TIMEOUT_S = 120.0  # tolerates slow cold-loading local models
DEFAULT_TURN_DEADLINE_S = 300.0  # total wait for a first answer across all fallback candidates
_USE_DEFAULT: Any = object()


class ProviderTimeout(TransientProviderError):
    """The gateway gave up waiting for one provider attempt. Never retried on the same provider."""


def _seconds_from_env(name: str, default: float | None) -> float | None:
    """Owner override: a number of seconds in (0, 3600]; ``0``/``off`` disables; junk -> default."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "off", "none", "false"}:
        return None
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and 0 < value <= 3600 else default


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
        stream_idle_timeout: float | None = _USE_DEFAULT,
        tool_call_policy: str = "annotate",
        attempt_timeout: float | None = _USE_DEFAULT,
        turn_deadline: float | None = _USE_DEFAULT,
    ) -> None:
        if tool_call_policy not in ("annotate", "filter"):
            raise ValueError("tool_call_policy must be 'annotate' or 'filter'")
        # annotate: report issues, pass calls through (the caller's broker decides);
        # filter: drop calls that fail screening against the offered tools.
        self.tool_call_policy = tool_call_policy
        if stream_idle_timeout is _USE_DEFAULT:
            stream_idle_timeout = _seconds_from_env(
                "MIA_GATEWAY_STREAM_IDLE_TIMEOUT_S", DEFAULT_STREAM_IDLE_TIMEOUT_S
            )
        if attempt_timeout is _USE_DEFAULT:
            attempt_timeout = _seconds_from_env(
                "MIA_GATEWAY_ATTEMPT_TIMEOUT_S", DEFAULT_ATTEMPT_TIMEOUT_S
            )
        if turn_deadline is _USE_DEFAULT:
            turn_deadline = _seconds_from_env(
                "MIA_GATEWAY_TURN_DEADLINE_S", DEFAULT_TURN_DEADLINE_S
            )
        for label, value in (
            ("stream_idle_timeout", stream_idle_timeout),
            ("attempt_timeout", attempt_timeout),
            ("turn_deadline", turn_deadline),
        ):
            if value is not None and not (
                isinstance(value, (int, float)) and math.isfinite(value) and 0 < value <= 3600
            ):
                raise ValueError(f"{label} must be a number of seconds from 0 to 3600")
        self.stream_idle_timeout = stream_idle_timeout
        self.attempt_timeout = attempt_timeout
        self.turn_deadline = turn_deadline
        self.providers = providers
        self.health = health or CandidateHealthTracker()
        self.model_registry = model_registry
        self.preferences = preferences or RoutingPreferences()
        self.decisions = decisions or DecisionLog()
        self.retry_policy = retry_policy or RetryPolicy()
        self.events = events or ResilienceLog()
        self.breaker = breaker or CircuitBreaker(on_event=self.events.add)
        self._sleep = sleep
        self.usage_ledger: Any | None = None
        self.require_budget_reservations = False

    def _new_deadline(self) -> float | None:
        """Monotonic instant by which this turn must have a first answer, or None (disabled)."""
        if self.turn_deadline is None:
            return None
        return time.monotonic() + self.turn_deadline

    async def _bounded(self, awaitable: Awaitable[Any], deadline: float | None = None) -> Any:
        """Await one provider attempt, converting a gateway-level timeout into ``ProviderTimeout``.

        The wait is the per-attempt timeout, clamped to what is left of the turn deadline.
        Consumer cancellation still propagates as ``CancelledError`` (it is not a timeout).
        """
        limits = [self.attempt_timeout] if self.attempt_timeout is not None else []
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                close = getattr(awaitable, "close", None)
                if close is not None:
                    close()  # never-started coroutine: avoid a "never awaited" warning
                raise ProviderTimeout("turn time budget spent")
            limits.append(remaining)
        if not limits:
            return await awaitable
        timeout = min(limits)
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except TimeoutError:
            raise ProviderTimeout(f"provider did not answer within {timeout:g}s") from None

    async def _call_with_retry(
        self,
        candidate_name: str,
        call: Callable[[], Awaitable[Any]],
        deadline: float | None = None,
    ) -> Any:
        """Run one provider call, retrying transient failures per the retry policy.

        Rate limits and authentication/permanent errors are never retried here. The
        retried operation is a pure generation request: nothing has been delivered
        to the caller or executed (tool calls run outside the gateway), so a retry
        cannot duplicate side effects.
        """
        attempts = self.retry_policy.max_attempts
        for attempt in range(attempts):
            try:
                return await self._bounded(call(), deadline)
            except ProviderTimeout:
                raise  # a hung provider is skipped, not retried: the owner is already waiting
            except TransientProviderError as exc:
                if exc.rate_limited or attempt + 1 >= attempts:
                    raise
                delay = self.retry_policy.delay(attempt)
                self.events.add(
                    "retry", candidate_name, f"attempt {attempt + 1} failed; retry in {delay:.1f}s"
                )
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _guard_idle(
        self, source: AsyncIterator[StreamChunk], deadline: float | None = None
    ) -> AsyncIterator[StreamChunk]:
        """Pass chunks through, failing transiently if the provider goes silent too long.

        Always closes the provider's stream, so the HTTP connection is released on
        timeout, error, cancellation or early close.
        """
        iterator = source.__aiter__()
        started_output = False
        try:
            while True:
                limits = [] if self.stream_idle_timeout is None else [self.stream_idle_timeout]
                if deadline is not None and not started_output:
                    # The turn deadline bounds the wait for a first answer only; a stream
                    # that is already producing is never cut off by it.
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderTimeout("turn time budget spent")
                    limits.append(remaining)
                try:
                    if not limits:
                        chunk = await iterator.__anext__()
                    else:
                        chunk = await asyncio.wait_for(iterator.__anext__(), min(limits))
                except StopAsyncIteration:
                    return
                except TimeoutError:
                    raise ProviderTimeout("stream idle timeout") from None
                started_output = True
                yield chunk
        finally:
            aclose = getattr(iterator, "aclose", None)
            if aclose is not None:
                await aclose()

    def _screen(
        self, request: ModelRequest, calls: tuple[dict[str, Any], ...]
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, object], ...]]:
        """Screen proposed tool calls against the offered tools; never executes anything."""
        if not calls:
            return calls, ()
        valid, issues = screen_tool_calls(calls, request.tools)
        report = tuple(issue.to_dict() for issue in issues)
        return (valid if self.tool_call_policy == "filter" else calls), report

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
        preferences = self.preferences
        if self.model_registry is not None:
            # Persisted per-model owner preferences survive restart and provider refresh.
            stored_preferences = {
                record.key: record.preference
                for record in self.model_registry.list(include_stale=False)
                if record.preference != 0
            }
            if stored_preferences:
                preferences = replace(
                    preferences,
                    preferences={**stored_preferences, **preferences.preferences},
                )
        ordered, decision = select(
            candidates, request, policy, preferences, hint=order_hint
        )
        self.decisions.add(decision)
        return ordered

    @staticmethod
    def _configured_budget(name: str) -> float | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None

    def _reserve_candidate_budget(
        self, candidate: ModelCandidate, request: ModelRequest, policy: RoutePolicy
    ) -> tuple[str | None, bool]:
        """Reserve this paid candidate before provider I/O when app-level enforcement is active."""
        if candidate.free_tier:
            return None, True
        if not policy.allow_paid:
            return None, False
        if self.usage_ledger is None:
            return (None, not self.require_budget_reservations)
        estimated = estimate_cost_usd(candidate, request)
        daily = self._configured_budget("MIA_MAX_DAILY_SPEND_USD")
        monthly = self._configured_budget("MIA_MAX_MONTHLY_SPEND_USD")
        if estimated is None or daily is None or monthly is None:
            return None, False
        if estimated <= 0:
            return None, True
        reservation_id = self.usage_ledger.reserve_budget(
            provider_id=candidate.provider_id,
            model_id=candidate.model_id,
            reserved_usd=estimated,
            per_request_limit_usd=policy.max_request_cost_usd,
            daily_limit_usd=daily,
            monthly_limit_usd=monthly,
        )
        return reservation_id, reservation_id is not None

    def _note_model_unavailable(self, candidate: ModelCandidate, name: str, exc: Exception) -> None:
        """Record a not-found model so later turns rank it after healthy candidates.

        Only the candidate is penalised; the provider is reachable, so the circuit
        breaker is untouched.
        """
        self.health.record_failure(candidate, TransientProviderError(str(exc)))
        self.events.add("model_unavailable", name, _brief(exc))

    def _note_empty_output(
        self, candidate_name: str, exc: Exception, invalid: list[str]
    ) -> None:
        """Record an unusable model reply; the provider answered, so it stays healthy."""
        self.events.add("invalid_output", candidate_name, _brief(exc))
        invalid.append(f"{candidate_name}: {exc}"[:160])

    def _release_budget_reservation(self, reservation_id: str | None) -> None:
        if reservation_id and self.usage_ledger is not None:
            self.usage_ledger.release_reservation(reservation_id)

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
        budget_blocked: list[str] = []
        invalid: list[str] = []
        unavailable: list[str] = []
        deadline = self._new_deadline()

        for candidate in ranked:
            provider_id = candidate.provider_id
            candidate_name = f"{provider_id}/{candidate.model_id}"
            if deadline is not None and time.monotonic() >= deadline:
                self.events.add("deadline", candidate_name, "turn time budget spent; not tried")
                if last_error is None:
                    last_error = ProviderTimeout("turn time budget spent")
                break
            if not self.breaker.allow(provider_id):
                if provider_id not in skipped:
                    skipped.append(provider_id)
                    self.events.add("skipped", provider_id, "circuit open; provider paused")
                continue
            reservation_id, budget_allowed = self._reserve_candidate_budget(
                candidate, request, policy
            )
            if not budget_allowed:
                budget_blocked.append(candidate_name)
                self.events.add("budget_blocked", candidate_name,
                                "paid request could not reserve the remaining spend allowance")
                continue
            attempted.append(candidate_name)
            provider = self.providers.get(provider_id)
            settled = False
            keep_reservation = False
            try:
                started = time.perf_counter()
                response = await self._call_with_retry(
                    candidate_name, lambda: provider.complete(candidate, request), deadline
                )
                latency_ms = (time.perf_counter() - started) * 1000.0
                if isinstance(response, str):
                    response = ProviderResponse(content=response)
                if not response.content and not response.tool_calls:
                    raise EmptyResponseError("Provider returned an empty response")
                tool_calls, tool_issues = self._screen(request, response.tool_calls)
                structured: Any = None
                problem: str | None = None
                if request.json_schema is not None:
                    structured, problem = parse_structured(
                        response.content or "", request.json_schema
                    )
                elif response.tool_calls and not tool_calls and not response.content:
                    problem = "model returned only invalid tool calls"
                # The provider answered, so it is healthy; the *model output* is what
                # failed. Try the next candidate without penalising the provider.
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                if problem is not None:
                    self.events.add("invalid_output", candidate_name, problem)
                    invalid.append(f"{candidate_name}: {problem}")
                    continue
                keep_reservation = True
                return CompletionResult(
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    content=response.content or "",
                    attempted_candidates=tuple(attempted),
                    tool_calls=tool_calls,
                    structured=structured,
                    tool_call_issues=tool_issues,
                    data_local=candidate.data_local,
                    usage=response.usage,
                    cost_usd=actual_cost_usd(candidate, response.usage, request),
                    free_tier=candidate.free_tier,
                    latency_ms=latency_ms,
                    reservation_id=reservation_id,
                )
            except asyncio.CancelledError:
                # The provider may already have processed billable input tokens.
                # Keep the reservation until expiry instead of silently refunding it.
                keep_reservation = reservation_id is not None
                raise
            except EmptyResponseError as exc:
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                keep_reservation = reservation_id is not None  # tokens may have been billed
                self._note_empty_output(candidate_name, exc, invalid)
                continue
            except ModelUnavailableError as exc:
                self._note_model_unavailable(candidate, candidate_name, exc)
                unavailable.append(candidate_name)
                continue
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
                if isinstance(exc, ProviderTimeout) and reservation_id is not None:
                    # The cancelled paid request may already have incurred charges; hold the
                    # reservation until it expires instead of refunding (same as cancellation).
                    keep_reservation = True
                last_error = exc
                continue
            except PermanentProviderError:
                raise
            finally:
                if not settled:
                    self.breaker.release(provider_id)
                if not keep_reservation:
                    self._release_budget_reservation(reservation_id)

        if invalid:
            raise PermanentProviderError(
                "No model produced a valid response: " + "; ".join(invalid[:3])
            )
        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
        if unavailable:
            raise ModelUnavailableError(
                "No eligible model could be found at its provider: " + ", ".join(unavailable[:3])
            )
        if budget_blocked:
            raise RuntimeError("Paid inference was blocked because its spend budget is reserved or exhausted")
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
        if request.json_schema is not None:
            raise ValueError("structured output (json_schema) requires complete(), not stream()")
        ranked = self._order_by_health(
            self._rank(candidates, request, policy, order_hint)
        )
        attempted: list[str] = []
        last_error: Exception | None = None
        skipped: list[str] = []
        budget_blocked: list[str] = []
        unavailable: list[str] = []
        deadline = self._new_deadline()
        invalid: list[str] = []

        for candidate in ranked:
            provider_id = candidate.provider_id
            candidate_name = f"{provider_id}/{candidate.model_id}"
            if deadline is not None and time.monotonic() >= deadline:
                self.events.add("deadline", candidate_name, "turn time budget spent; not tried")
                if last_error is None:
                    last_error = ProviderTimeout("turn time budget spent")
                break
            if not self.breaker.allow(provider_id):
                if provider_id not in skipped:
                    skipped.append(provider_id)
                    self.events.add("skipped", provider_id, "circuit open; provider paused")
                continue
            reservation_id, budget_allowed = self._reserve_candidate_budget(
                candidate, request, policy
            )
            if not budget_allowed:
                budget_blocked.append(candidate_name)
                self.events.add("budget_blocked", candidate_name,
                                "paid stream could not reserve the remaining spend allowance")
                continue
            attempted.append(candidate_name)
            provider = self.providers.get(provider_id)
            settled = False
            keep_reservation = False
            emitted = False
            text_sent: list[str] = []
            chunk_iter: AsyncIterator[StreamChunk] | None = None
            streamer = getattr(provider, "stream", None)
            started = time.perf_counter()
            first_token_at: float | None = None
            last_chunk_at = started
            # Time this generator spends suspended at a yield belongs to the
            # consumer, not the provider; it is excluded from the measurements.
            paused = 0.0
            paused_at_last_chunk = 0.0
            try:
                buffered = streamer is None or (
                    bool(request.tools) and not getattr(provider, "streams_tools", False)
                )
                if buffered:
                    response = await self._bounded(provider.complete(candidate, request), deadline)
                    last_chunk_at = first_token_at = time.perf_counter()
                    if isinstance(response, str):
                        response = ProviderResponse(content=response)
                    if not response.content and not response.tool_calls:
                        raise EmptyResponseError("Provider returned an empty response")
                    chunks: list[StreamChunk] = [
                        StreamChunk(
                            delta=response.content or "",
                            finish_reason=response.finish_reason or "stop",
                            usage=response.usage,
                            tool_calls=response.tool_calls,
                        )
                    ]
                    for chunk in chunks:
                        if not chunk.delta:
                            continue
                        emitted = True
                        text_sent.append(chunk.delta)
                        yielded_at = time.perf_counter()
                        yield StreamEvent(delta=chunk.delta)
                        paused += time.perf_counter() - yielded_at
                    final = chunks[-1]
                else:
                    final = StreamChunk()
                    text_parts: list[str] = []
                    chunk_iter = self._guard_idle(streamer(candidate, request), deadline)
                    async for chunk in chunk_iter:
                        # Timestamps are taken on receipt, before yielding to the
                        # consumer, so a slow consumer does not inflate latency.
                        last_chunk_at = time.perf_counter()
                        paused_at_last_chunk = paused
                        if chunk.delta:
                            if first_token_at is None:
                                first_token_at = last_chunk_at
                            emitted = True
                            text_parts.append(chunk.delta)
                            text_sent.append(chunk.delta)
                            yielded_at = time.perf_counter()
                            yield StreamEvent(delta=chunk.delta)
                            paused += time.perf_counter() - yielded_at
                        if chunk.finish_reason or chunk.usage or chunk.tool_calls:
                            final = chunk
                    if not text_parts and not final.tool_calls:
                        raise EmptyResponseError("Provider returned an empty response")
                out_calls, out_issues = self._screen(request, final.tool_calls)
                if final.tool_calls and not out_calls and not text_sent:
                    raise EmptyResponseError("model returned only invalid tool calls")
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                keep_reservation = True
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
                    tool_calls=out_calls,
                    tool_call_issues=out_issues,
                    reservation_id=reservation_id,
                )
                return
            except (asyncio.CancelledError, GeneratorExit):
                # A dispatched paid stream may have incurred input/output charges
                # even when the consumer cancels before the final usage event.
                # Retain its reservation until TTL rather than silently refunding it.
                keep_reservation = reservation_id is not None
                raise
            except EmptyResponseError as exc:
                if emitted:
                    raise
                self.health.record_success(candidate)
                self.breaker.record_success(provider_id)
                settled = True
                keep_reservation = reservation_id is not None
                self._note_empty_output(candidate_name, exc, invalid)
                continue
            except ModelUnavailableError as exc:
                if emitted:
                    raise
                self._note_model_unavailable(candidate, candidate_name, exc)
                unavailable.append(candidate_name)
                continue
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
                if isinstance(exc, ProviderTimeout) and reservation_id is not None:
                    keep_reservation = True  # possibly billed before we gave up; hold until expiry
                if emitted:
                    # Keep the partial text with the error so callers can preserve it.
                    raise StreamInterruptedError(
                        str(exc),
                        partial_text="".join(text_sent),
                        retry_after=exc.retry_after,
                        rate_limited=exc.rate_limited,
                    ) from exc
                last_error = exc
                continue
            finally:
                if chunk_iter is not None:
                    aclose = getattr(chunk_iter, "aclose", None)
                    if aclose is not None:
                        await aclose()
                if not settled:
                    self.breaker.release(provider_id)
                if not keep_reservation:
                    self._release_budget_reservation(reservation_id)

        if invalid:
            raise PermanentProviderError(
                "No model produced a valid response: " + "; ".join(invalid[:3])
            )
        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
        if unavailable:
            raise ModelUnavailableError(
                "No eligible model could be found at its provider: " + ", ".join(unavailable[:3])
            )
        if budget_blocked:
            raise RuntimeError("Paid inference was blocked because its spend budget is reserved or exhausted")
        if skipped:
            raise TransientProviderError(
                "All eligible providers are temporarily paused: " + ", ".join(skipped)
            )
        raise RuntimeError("No eligible model is available under the current routing policy")
