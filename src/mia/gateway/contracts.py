"""Provider-neutral contracts; concrete network adapters are added in Phase 2."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelRequest:
    task_type: str
    prompt: str
    estimated_input_tokens: int = 0
    max_output_tokens: int = 1024
    required_capabilities: frozenset[str] = frozenset()


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
    input_cost_per_million_usd: float = 0.0
    output_cost_per_million_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    allow_paid: bool = False
    max_request_cost_usd: float = 0.0


class ModelProvider(Protocol):
    """Interface implemented by each local or cloud provider adapter."""

    async def complete(self, request: ModelRequest) -> str:
        """Return a model response for a validated request."""
        ...
