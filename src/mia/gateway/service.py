"""Policy-controlled model selection and safe text-completion fallback."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import (
    ModelCandidate,
    ModelRequest,
    PermanentProviderError,
    RoutePolicy,
    TransientProviderError,
)
from .registry import ProviderRegistry
from .routing import rank_candidates


@dataclass(frozen=True, slots=True)
class CompletionResult:
    provider_id: str
    model_id: str
    content: str
    attempted_candidates: tuple[str, ...]


class ModelGateway:
    def __init__(self, providers: ProviderRegistry) -> None:
        self.providers = providers

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
    ) -> CompletionResult:
        ranked = rank_candidates(candidates, request, policy)
        attempted: list[str] = []
        last_error: Exception | None = None

        for candidate in ranked:
            attempted.append(f"{candidate.provider_id}/{candidate.model_id}")
            provider = self.providers.get(candidate.provider_id)
            try:
                content = await provider.complete(candidate, request)
                return CompletionResult(
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    content=content,
                    attempted_candidates=tuple(attempted),
                )
            except TransientProviderError as exc:
                last_error = exc
                continue
            except PermanentProviderError:
                raise

        if last_error:
            raise TransientProviderError(
                f"All eligible candidates failed. Tried: {', '.join(attempted)}"
            ) from last_error
        raise RuntimeError("No eligible model is available under the current routing policy")
