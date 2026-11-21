"""Evidence-backed runtime self-description for Mia.

Only names and registration states are exposed. Credentials, environment values,
filesystem paths, provider health claims and owner-verification claims are excluded.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from mia.sps.layers import SPS_LAYERS

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z][a-z0-9]*)*$")


def _safe_names(values: Iterable[Any], *, limit: int = 30) -> list[str]:
    names = sorted({
        value.strip()
        for value in values
        if isinstance(value, str) and _SAFE_NAME.fullmatch(value.strip().lower())
    })
    return names[:limit]


# Reasons offline wake-word detection can be unavailable (see mia.voice.wake). Anything
# else is reported as "unspecified" so untrusted text never reaches the system prompt.
_WAKE_REASONS = frozenset(
    {"engine_not_installed", "model_not_configured", "audio_transport_not_available"}
)
_WAKE_COMPONENTS = ("engine_installed", "model_configured", "audio_transport_available")


def _offline_wake_word_summary(status: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return (overall, detail) from operational evidence only.

    Route paths and tool names are never evidence: a path like ``/api/voice/wake/status``
    proves a route exists, not that detection works. "Operational" requires the status
    report to say so with a literal ``True`` *and* engine, model and audio transport to
    each be literally ``True``. Without any status, nothing is claimed.
    """
    if not isinstance(status, Mapping):
        return "not registered", "not registered"
    if status.get("operational") is True and all(status.get(k) is True for k in _WAKE_COMPONENTS):
        return "operational", "operational (explicit opt-in; not always listening)"
    reason = status.get("reason")
    reason = reason if isinstance(reason, str) and reason in _WAKE_REASONS else "unspecified"
    return "not operational", f"not operational ({reason})"


def build_runtime_manifest(
    *,
    tool_names: Iterable[str],
    provider_ids: Iterable[str],
    route_paths: Iterable[str],
    memory_enabled: bool,
    semantic_memory_enabled: bool,
    permission_broker_enabled: bool,
    capability_registry_enabled: bool,
    offline_wake_word: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic manifest from live registries, never from model claims.

    ``offline_wake_word`` is the report from ``mia.voice.wake.offline_wake_word_status``.
    """
    tools = _safe_names(tool_names)
    providers = _safe_names(provider_ids)
    routes = {path for path in route_paths if isinstance(path, str)}
    computer_tools = [name for name in tools if name.startswith(
        ("computer.", "desktop.", "filesystem.", "browser.")
    )]
    voice_registered = any("/api/voice" in path or "/api/tts" in path for path in routes)
    code_graph_registered = "/api/code-graph" in routes
    browser_wake_phrase_available = "/assistant" in routes
    wake_overall, wake_detail = _offline_wake_word_summary(offline_wake_word)
    return {
        "tools": tools,
        "tool_count": len(tools),
        "providers": providers,
        "provider_count": len(providers),
        "computer_tools": computer_tools,
        "memory_store": "active" if memory_enabled else "not registered",
        "semantic_memory": "enabled" if semantic_memory_enabled else "disabled",
        "permission_broker": "active" if permission_broker_enabled else "not registered",
        "capability_registry": "active" if capability_registry_enabled else "not registered",
        "voice_routes": "registered" if voice_registered else "not registered",
        "code_graph": "registered" if code_graph_registered else "not registered",
        "wake_word": wake_overall,
        "browser_wake_phrase": (
            "available (explicit opt-in; browser speech service)"
            if browser_wake_phrase_available else "not registered"
        ),
        "offline_wake_word": wake_detail,
        "sps_framework": {
            "name": "Self Programming Software (SPS)",
            "layer_count": len(SPS_LAYERS),
            "layers": [
                {
                    "id": layer.layer_id,
                    "name": layer.name,
                    "responsibility": layer.responsibility,
                }
                for layer in SPS_LAYERS
            ],
            "implementation_status": (
                "The canonical ten-layer metadata contract is registered; "
                "the full ten-layer implementation is partial and in progress."
            ),
            "runtime_components": [
                "coordinator",
                "API server",
                "scheduler",
                "user interface",
                "provider gateway",
                "computer adapters",
                "memory stores",
                "observatory",
            ],
        },
    }


def render_runtime_manifest(manifest: dict[str, Any]) -> str:
    """Render concise trusted facts for the Brain system prompt."""
    tools = ", ".join(manifest.get("tools", [])) or "none registered"
    providers = ", ".join(manifest.get("providers", [])) or "none registered"
    computer = ", ".join(manifest.get("computer_tools", [])) or "none registered"
    sps = manifest.get("sps_framework", {})
    layers = sps.get("layers", [])
    layer_summary = ", ".join(
        f"{layer.get('id', '?')} {layer.get('name', 'unknown')}"
        for layer in layers
    ) or "not registered"
    layer_responsibilities = "; ".join(
        f"{layer.get('id', '?')}: {layer.get('responsibility', 'not specified')}"
        for layer in layers
    ) or "not registered"
    return "\n".join([
        "LIVE RUNTIME EVIDENCE (generated from Mia's current application registries):",
        f"- Registered tools ({manifest.get('tool_count', 0)}): {tools}",
        f"- Configured provider adapters ({manifest.get('provider_count', 0)}): {providers}",
        f"- Computer/browser/filesystem tool registrations: {computer}",
        f"- Persistent memory store: {manifest.get('memory_store', 'unknown')}",
        f"- Local semantic memory reranking: {manifest.get('semantic_memory', 'unknown')}",
        f"- Owner permission broker: {manifest.get('permission_broker', 'unknown')}",
        f"- Capability registry: {manifest.get('capability_registry', 'unknown')}",
        f"- Voice API routes: {manifest.get('voice_routes', 'unknown')}",
        f"- Code Graph API: {manifest.get('code_graph', 'unknown')}",
        f"- Browser wake phrase: {manifest.get('browser_wake_phrase', 'unknown')}",
        f"- Offline wake-word detection: {manifest.get('offline_wake_word', 'unknown')}",
        f"- Architecture: {sps.get('name', 'unknown')} with {sps.get('layer_count', 0)} canonical layers: {layer_summary}",
        f"- SPS layer responsibilities: {layer_responsibilities}",
        f"- SPS implementation status: {sps.get('implementation_status', 'unknown')}",
        "- Runtime services (API, scheduler, UI, gateway, adapters, memory and observatory) support the SPS layers; they are not extra SPS layers.",
        "SELF-DESCRIPTION RULE: When asked what Mia is or can do, explain the SPS framework and these runtime facts; clearly separate registered capabilities from unavailable or planned features.",
        "EVIDENCE LIMITS: Registration does not prove provider health, model availability, device access, permission approval, or owner verification. Check the live operation result before claiming an action succeeded. Never invent capabilities or expose credentials, tokens, environment values, private paths, or raw configuration.",
    ])
