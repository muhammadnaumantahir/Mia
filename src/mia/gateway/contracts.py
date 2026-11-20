"""Provider-neutral contracts for local and cloud inference."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task_type: str
    prompt: str
    estimated_input_tokens: int = 0
    max_output_tokens: int = 1024
    required_capabilities: frozenset[str] = frozenset()
    messages: tuple[dict[str, Any], ...] = ()
    tools: tuple[dict[str, Any], ...] = ()
    sensitive: bool = False  # private content: may be restricted to local models


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counts reported by a provider for one completion."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str | None = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """One streamed fragment; usage/finish_reason arrive on the last chunk."""

    delta: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    # Complete, normalized tool calls; delivered once on the last chunk, never as
    # partial fragments (arguments are only meaningful when whole).
    tool_calls: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    provider_id: str
    model_id: str
    capabilities: frozenset[str]
    context_window: int
    free_tier: bool
    healthy: bool
    enabled: bool
    reliability_score: float = 0.5
    latency_ms: float = 10_000.0
    quota_remaining: float | None = None
    input_cost_per_million_usd: float | None = None
    output_cost_per_million_usd: float | None = None
    data_local: bool = False


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    allow_paid: bool = False
    max_request_cost_usd: float = 0.0


class TransientProviderError(RuntimeError):
    """A provider failure for which trying the next eligible candidate is safe.

    ``rate_limited`` marks an explicit provider throttle (HTTP 429, or a 503
    that carries Retry-After); ``retry_after`` is the provider-advised wait in
    seconds. Invalid values (negative, NaN, infinite) are discarded, never trusted.
    """

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message)
        valid = (
            isinstance(retry_after, (int, float))
            and not isinstance(retry_after, bool)
            and retry_after == retry_after  # not NaN
            and 0 <= retry_after < float("inf")
        )
        self.retry_after: float | None = float(retry_after) if valid else None
        self.rate_limited = bool(rate_limited)


class PermanentProviderError(RuntimeError):
    """A non-transient provider failure that should not be retried automatically."""


class StreamInterruptedError(TransientProviderError):
    """A stream failed after text was already delivered; ``partial_text`` is what was sent."""

    def __init__(
        self,
        message: str = "",
        *,
        partial_text: str = "",
        retry_after: float | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(message, retry_after=retry_after, rate_limited=rate_limited)
        self.partial_text = partial_text


class AuthenticationProviderError(PermanentProviderError):
    """The provider rejected the credentials (HTTP 401/403); never retried."""


class ModelProvider(Protocol):
    provider_id: str

    async def health(self) -> bool:
        """Return whether the provider endpoint is reachable and ready."""
        ...

    async def list_models(self) -> list[ModelCandidate]:
        """Return currently available models with conservative metadata."""
        ...

    async def complete(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> ProviderResponse | str:
        """Return assistant text and optional structured tool calls."""
        ...


class StreamingModelProvider(ModelProvider, Protocol):
    def stream(
        self,
        candidate: ModelCandidate,
        request: ModelRequest,
    ) -> AsyncIterator[StreamChunk]:
        """Yield text fragments as the model produces them."""
        ...
