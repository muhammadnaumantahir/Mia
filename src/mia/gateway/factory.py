"""Build Mia's default provider registry from safe local configuration."""

from __future__ import annotations

import os

from .credentials import CredentialStore
from .providers import OllamaProvider, OpenAICompatibleProvider
from .registry import ProviderRegistry


def build_default_registry() -> ProviderRegistry:
    """Register Ollama by default and FreeLLMAPI only when explicitly enabled."""
    registry = ProviderRegistry()
    ollama_url = os.getenv("MIA_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    registry.register(OllamaProvider(base_url=ollama_url))

    if os.getenv("MIA_FREELLMAPI_ENABLED", "false").strip().lower() == "true":
        base_url = os.getenv(
            "MIA_FREELLMAPI_BASE_URL",
            "http://127.0.0.1:3001/v1",
        )
        secret = CredentialStore().get("freellmapi")
        if not secret:
            raise RuntimeError(
                "FreeLLMAPI is enabled but no key is saved in the OS credential store. "
                "Run: mia-credentials set freellmapi"
            )
        # The default is intentionally not free. Set MIA_FREELLMAPI_ROUTE_FREE=true
        # only after confirming the local gateway's active chains cannot bill.
        route_is_free = (
            os.getenv("MIA_FREELLMAPI_ROUTE_FREE", "false").strip().lower()
            == "true"
        )
        registry.register(
            OpenAICompatibleProvider(
                "freellmapi",
                base_url,
                api_key=secret,
                free_tier=route_is_free,
            )
        )

    return registry


def paid_route_policy():
    """Return a budget policy; paid routes remain disabled unless explicitly enabled."""
    from .contracts import RoutePolicy

    enabled = os.getenv("MIA_ALLOW_PAID", "false").strip().lower() == "true"
    cap = float(os.getenv("MIA_MAX_REQUEST_SPEND_USD", "0"))
    return RoutePolicy(allow_paid=enabled, max_request_cost_usd=max(0.0, cap))
