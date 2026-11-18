"""Provider-neutral contracts for local and cloud inference."""

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


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str | None = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    finish_reason: str | None = None


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
    """A provider failure for which trying the next eligible candidate is safe."""


class PermanentProviderError(RuntimeError):
    """A non-transient provider failure that should not be retried automatically."""


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
