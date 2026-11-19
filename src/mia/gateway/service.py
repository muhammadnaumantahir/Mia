"""Policy-controlled model selection and safe text-completion fallback."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from .contracts import (
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
from .registry import ProviderRegistry
from .routing import actual_cost_usd, rank_candidates


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
    ) -> None:
        self.providers = providers
        self.health = health or CandidateHealthTracker()

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
        for provider in self.providers.values():
            try:
                candidates.extend(await provider.list_models())
            except TransientProviderError:
                continue
        return candidates

    async def complete(
        self,
        request: ModelRequest,
        candidates: list[ModelCandidate],
        policy: RoutePolicy = RoutePolicy(),
        order_hint: list[ModelCandidate] | tuple[ModelCandidate, ...] | None = None,
    ) -> CompletionResult:
        ranked = self._order_by_health(
            self._apply_order_hint(rank_candidates(candidates, request, policy), order_hint)
        )
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in ranked:
            attempted.append(f"{candidate.provider_id}/{candidate.model_id}")
            provider = self.providers.get(candidate.provider_id)
            try:
                started = time.perf_counter()
                response = await provider.complete(candidate, request)
                latency_ms = (time.perf_counter() - started) * 1000.0
                if isinstance(response, str):
                    response = ProviderResponse(content=response)
                if not response.content and not response.tool_calls:
                    raise PermanentProviderError("Provider returned an empty response")
                self.health.record_success(candidate)
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
            except TransientProviderError as exc:
                self.health.record_failure(candidate, exc)
                last_error = exc
                continue
            except PermanentProviderError:
                raise

        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
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
            self._apply_order_hint(rank_candidates(candidates, request, policy), order_hint)
        )
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in ranked:
            attempted.append(f"{candidate.provider_id}/{candidate.model_id}")
            provider = self.providers.get(candidate.provider_id)
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
            except TransientProviderError as exc:
                self.health.record_failure(candidate, exc)
                if emitted:
                    raise
                last_error = exc
                continue

        if last_error:
            raise _aggregate_failure(attempted, last_error) from last_error
        raise RuntimeError("No eligible model is available under the current routing policy")
