from mia.brain.self_description import build_runtime_manifest, render_runtime_manifest


def test_runtime_manifest_uses_live_registries_and_reports_limits():
    manifest = build_runtime_manifest(
        tool_names=["browser.open", "filesystem.read", "computer.click", "sk-proj-123456789"],
        provider_ids=["ollama", "openai-compatible", "sk-proj-123456789"],
        route_paths=["/assistant", "/api/chat", "/api/voice/transcribe", "/api/code-graph"],
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
    assert manifest["browser_wake_phrase"].startswith("available")
    assert manifest["offline_wake_word"] == "not registered"
    assert manifest["memory_store"] == "active"
    assert manifest["semantic_memory"] == "disabled"
    framework = manifest["sps_framework"]
    assert framework["name"] == "Self Programming Software (SPS)"
    assert framework["layer_count"] == 10
    assert [layer["id"] for layer in framework["layers"]] == [
        f"SPS-{number:02d}" for number in range(1, 11)
    ]
    assert framework["layers"][0]["name"] == "Software DNA Core"
    assert framework["layers"][-1]["name"] == "Execution Core"
    assert "partial" in framework["implementation_status"]

    prompt = render_runtime_manifest(manifest)
    assert "EVIDENCE LIMITS" in prompt
    assert "sk-proj-123456789" not in prompt
    assert "never invent capabilities" in prompt.lower()
    assert "Self Programming Software (SPS)" in prompt
    assert "SPS-01 Software DNA Core" in prompt
    assert "SPS-10 Execution Core" in prompt
    assert "SPS-02: Permissions, policy, risk, budgets and approvals." in prompt
    assert "SPS-09: Checks, tests, security validation and benchmarks." in prompt
    assert "not extra SPS layers" in prompt
    assert "SELF-DESCRIPTION RULE" in prompt
    assert "separate registered capabilities from unavailable or planned features" in prompt
    assert "Browser wake phrase: available" in prompt
    assert "Offline wake-word detection: not registered" in prompt


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
    assert first["browser_wake_phrase"] == "not registered"
