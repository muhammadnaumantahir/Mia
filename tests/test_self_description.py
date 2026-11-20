from mia.brain.self_description import build_runtime_manifest, render_runtime_manifest


def test_runtime_manifest_uses_live_registries_and_reports_limits():
    manifest = build_runtime_manifest(
        tool_names=["browser.open", "filesystem.read", "computer.click", "sk-proj-123456789"],
        provider_ids=["ollama", "openai-compatible", "sk-proj-123456789"],
        route_paths=["/api/chat", "/api/voice/transcribe", "/api/code-graph"],
        memory_enabled=True,
        semantic_memory_enabled=False,
        permission_broker_enabled=True,
        capability_registry_enabled=True,
    )
    assert manifest["tools"] == ["browser.open", "computer.click", "filesystem.read"]
    assert manifest["providers"] == ["ollama", "openai-compatible"]
    assert manifest["voice_routes"] == "registered"
    assert manifest["code_graph"] == "registered"
    assert manifest["wake_word"] == "not registered"
    assert manifest["memory_store"] == "active"
    assert manifest["semantic_memory"] == "disabled"

    prompt = render_runtime_manifest(manifest)
    assert "EVIDENCE LIMITS" in prompt
    assert "sk-proj-123456789" not in prompt
    assert "never invent capabilities" in prompt.lower()


def test_runtime_manifest_is_deterministic():
    kwargs = dict(
        provider_ids=["ollama", "openai"],
        route_paths=[],
        memory_enabled=True,
        semantic_memory_enabled=False,
        permission_broker_enabled=True,
        capability_registry_enabled=True,
    )
    first = build_runtime_manifest(tool_names=["zeta.tool", "alpha.tool"], **kwargs)
    second = build_runtime_manifest(tool_names=["alpha.tool", "zeta.tool"], **kwargs)
    assert first == second
