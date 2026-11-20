"""Evidence-backed runtime self-description for Mia.

Only names and registration states are exposed. Credentials, environment values,
filesystem paths, provider health claims and owner-verification claims are excluded.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z][a-z0-9]*)*$")


def _safe_names(values: Iterable[Any], *, limit: int = 30) -> list[str]:
    names = sorted({
        value.strip()
        for value in values
        if isinstance(value, str) and _SAFE_NAME.fullmatch(value.strip().lower())
    })
    return names[:limit]


def build_runtime_manifest(
    *,
    tool_names: Iterable[str],
    provider_ids: Iterable[str],
    route_paths: Iterable[str],
    memory_enabled: bool,
    semantic_memory_enabled: bool,
    permission_broker_enabled: bool,
    capability_registry_enabled: bool,
) -> dict[str, Any]:
    """Build a deterministic manifest from live registries, never from model claims."""
    tools = _safe_names(tool_names)
    providers = _safe_names(provider_ids)
    routes = {path for path in route_paths if isinstance(path, str)}
    computer_tools = [name for name in tools if name.startswith(
        ("computer.", "desktop.", "filesystem.", "browser.")
    )]
    voice_registered = any("/api/voice" in path or "/api/tts" in path for path in routes)
    code_graph_registered = "/api/code-graph" in routes
    wake_word_registered = (
        any("wake" in path.lower() for path in routes)
        or any("wake" in name for name in tools)
    )
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
        "wake_word": "registered" if wake_word_registered else "not registered",
    }


def render_runtime_manifest(manifest: dict[str, Any]) -> str:
    """Render concise trusted facts for the Brain system prompt."""
    tools = ", ".join(manifest.get("tools", [])) or "none registered"
    providers = ", ".join(manifest.get("providers", [])) or "none registered"
    computer = ", ".join(manifest.get("computer_tools", [])) or "none registered"
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
        f"- Wake-word route/tool: {manifest.get('wake_word', 'unknown')}",
        "EVIDENCE LIMITS: Registration does not prove provider health, model availability, device access, permission approval, or owner verification. Check the live operation result before claiming an action succeeded. Never invent capabilities or expose credentials, tokens, environment values, private paths, or raw configuration.",
    ])
