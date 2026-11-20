"""Build Mia's default provider registry from safe local configuration."""

from __future__ import annotations

import json
import math
import os
import re

from .credentials import CredentialStore
from .providers import OllamaProvider, OpenAICompatibleProvider
from .registry import ProviderRegistry
from .urlpolicy import parse_timeout_seconds


def _flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() == "true"


def _register_openai_compatible(
    registry: ProviderRegistry,
    provider_id: str,
    prefix: str,
    default_url: str | None,
    *,
    force_enabled: bool = False,
) -> None:
    """Register one OpenAI-compatible provider from MIA_<PREFIX>_* variables.

    Variables: ENABLED, BASE_URL, ROUTE_FREE, DATA_LOCAL, ENABLE_TOOL_CALLING,
    TIMEOUT_SECONDS, HEADERS (JSON object of non-secret headers). The API key
    comes only from the OS credential store entry named after the provider.
    Providers are not free and not local unless the owner says so.
    """
    env = f"MIA_{prefix}_"
    if not (force_enabled or _flag(env + "ENABLED")):
        return
    base_url = os.getenv(env + "BASE_URL", default_url or "").strip()
    if not base_url:
        raise RuntimeError(f"{provider_id} is enabled but {env}BASE_URL is not set.")
    secret = CredentialStore().get(provider_id)
    if not secret:
        raise RuntimeError(
            f"{provider_id} is enabled but no key is saved in the OS credential store. "
            f"Run: mia-credentials set {provider_id}"
        )
    headers: dict[str, str] | None = None
    raw_headers = os.getenv(env + "HEADERS", "").strip()
    if raw_headers:
        try:
            parsed = json.loads(raw_headers)
        except ValueError as exc:
            raise RuntimeError(f"{env}HEADERS must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(f"{env}HEADERS must be a JSON object")
        headers = parsed
    registry.register(
        OpenAICompatibleProvider(
            provider_id,
            base_url,
            api_key=secret,
            timeout=parse_timeout_seconds(os.getenv(env + "TIMEOUT_SECONDS"), 90.0),
            free_tier=_flag(env + "ROUTE_FREE"),
            supports_tools=_flag(env + "ENABLE_TOOL_CALLING"),
            data_local=_flag(env + "DATA_LOCAL"),
            supports_json_schema=_flag(env + "ENABLE_JSON_SCHEMA"),
            extra_headers=headers,
        )
    )


def build_default_registry() -> ProviderRegistry:
    """Register Ollama by default and FreeLLMAPI only when explicitly enabled."""
    registry = ProviderRegistry()
    ollama_url = os.getenv("MIA_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_tools = (
        os.getenv("MIA_OLLAMA_ENABLE_TOOL_CALLING", "false").strip().lower() == "true"
    )
    registry.register(
        OllamaProvider(base_url=ollama_url, supports_tools=ollama_tools)
    )

    # FreeLLMAPI keeps its original variables; Omniroute follows the same scheme.
    # Omniroute has no default URL: its endpoint is not assumed, the owner sets it.
    _register_openai_compatible(registry, "freellmapi", "FREELLMAPI", "http://127.0.0.1:3001/v1")
    _register_openai_compatible(registry, "omniroute", "OMNIROUTE", None)

    # Further OpenAI-compatible endpoints: MIA_EXTRA_PROVIDERS=id1,id2 with
    # MIA_PROVIDER_<ID>_BASE_URL etc. (ID upper-cased, '-' becomes '_').
    for raw_id in os.getenv("MIA_EXTRA_PROVIDERS", "").split(","):
        provider_id = raw_id.strip().lower()
        if not provider_id:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", provider_id) or provider_id in (
            "ollama",
            "freellmapi",
            "omniroute",
        ):
            raise RuntimeError(f"Invalid or reserved extra provider id: {provider_id!r}")
        prefix = "PROVIDER_" + provider_id.upper().replace("-", "_")
        _register_openai_compatible(registry, provider_id, prefix, None, force_enabled=True)

    return registry


def paid_route_policy():
    """Return a budget policy; paid routes remain disabled unless explicitly enabled."""
    from .contracts import RoutePolicy

    enabled = os.getenv("MIA_ALLOW_PAID", "false").strip().lower() == "true"
    try:
        cap = float(os.getenv("MIA_MAX_REQUEST_SPEND_USD", "0"))
    except ValueError:
        return RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    if not math.isfinite(cap) or cap < 0:
        return RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    return RoutePolicy(allow_paid=enabled, max_request_cost_usd=cap)


def apply_daily_cap(policy, spent_today_usd: float):
    """Tighten a route policy by the remaining daily budget (MIA_MAX_DAILY_SPEND_USD).

    Unset means no daily cap (the per-request cap still applies). A malformed,
    non-finite or negative cap, or an unusable ledger total, disables paid
    routing. Free and local routes are never affected.
    """
    from .contracts import RoutePolicy

    raw = os.getenv("MIA_MAX_DAILY_SPEND_USD", "").strip()
    if not raw or not policy.allow_paid:
        return policy
    try:
        cap = float(raw)
    except ValueError:
        return RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    if (
        not math.isfinite(cap)
        or cap < 0
        or not math.isfinite(spent_today_usd)
        or spent_today_usd < 0
    ):
        return RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    remaining = cap - spent_today_usd
    if remaining <= 0:
        return RoutePolicy(allow_paid=False, max_request_cost_usd=0.0)
    return RoutePolicy(
        allow_paid=True,
        max_request_cost_usd=min(policy.max_request_cost_usd, remaining),
    )
