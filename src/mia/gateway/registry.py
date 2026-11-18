"""Provider registry inspired by the typed registry pattern used by OpenJarvis."""

from __future__ import annotations

from .contracts import ModelProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}

    def register(self, provider: ModelProvider) -> None:
        provider_id = provider.provider_id.strip().lower()
        if not provider_id:
            raise ValueError("provider_id is required")
        if provider_id in self._providers:
            raise ValueError(f"provider already registered: {provider_id}")
        self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id.strip().lower()]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {provider_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def values(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))
