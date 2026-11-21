"""The live runtime-evidence block must stay small unless the turn is about Mia itself
(MIA-BRAIN-07).

Default turns get a compact block (statuses, counts, the honesty rules). The full block
(tool names, SPS layer responsibilities) is attached only when the prompt asks about
Mia's identity, capabilities, tools, voice/wake word, providers or architecture, in
English, Roman Urdu or Urdu. Compacting must never remove a safety or honesty rule.
"""

import pytest

from mia.brain.self_description import (
    build_runtime_manifest,
    render_runtime_manifest,
    wants_runtime_detail,
)

from .test_brain_pipeline import Scripted, chat, client_for
from .test_identity import persona_of

TOOLS = [f"{ns}.{name}" for ns in ("browser", "computer", "filesystem", "desktop", "memory")
         for name in ("open", "read", "write", "click", "type", "search", "list", "scroll")]
KW = dict(
    provider_ids=["ollama", "openai-compatible"],
    route_paths=["/assistant", "/api/chat", "/api/voice/transcribe", "/api/code-graph"],
    memory_enabled=True, semantic_memory_enabled=False,
    permission_broker_enabled=True, capability_registry_enabled=True,
)


def manifest():
    return build_runtime_manifest(tool_names=TOOLS, **KW)


# ------------------------------------------------------------------ rendering


def test_full_rendering_is_unchanged_and_remains_the_default():
    full = render_runtime_manifest(manifest())
    assert render_runtime_manifest(manifest(), detailed=True) == full
    assert "browser.open" in full and "SPS layer responsibilities:" in full


def test_compact_block_is_much_smaller_than_the_full_block():
    full = render_runtime_manifest(manifest())
    compact = render_runtime_manifest(manifest(), detailed=False)
    # the two fixed honesty rules alone are about 680 characters, so this is a real floor
    assert len(compact) <= 1600, len(compact)
    assert len(compact) < 0.55 * len(full), (len(compact), len(full))


def test_compact_block_omits_enumerations_but_keeps_counts():
    compact = render_runtime_manifest(manifest(), detailed=False)
    assert "browser.open" not in compact and "SPS layer responsibilities:" not in compact
    assert f"Registered tools ({manifest()['tool_count']})" in compact
    assert "Configured provider adapters (2): ollama, openai-compatible" in compact


@pytest.mark.parametrize("needle", [
    "LIVE RUNTIME EVIDENCE",
    "SELF-DESCRIPTION RULE",
    "EVIDENCE LIMITS",
    "Registration does not prove provider health",
    "Never invent capabilities",
    "Offline wake-word detection: not registered",
    "Browser wake phrase: available",
    "Persistent memory store: active",
    "Owner permission broker: active",
    "10 canonical layers",
    "not extra SPS layers",
])
def test_compact_block_never_drops_an_honesty_rule_or_a_capability_status(needle):
    assert needle in render_runtime_manifest(manifest(), detailed=False)


def test_compact_block_says_where_the_details_are():
    assert "when asked" in render_runtime_manifest(manifest(), detailed=False).lower()


# ------------------------------------------------------------------ when detail is attached


@pytest.mark.parametrize("prompt", [
    "Who are you?",
    "What can you do?",
    "What tools do you have access to?",
    "Which tools are actually available right now?",
    "Tell me about your architecture and the SPS layers.",
    "Can you listen for a wake word?",
    "Do you have voice or a microphone?",
    "Which model providers are you using?",
    "What are your limitations?",
    "How do you work?",
    "Explain yourself.",
    "Tum kaun ho?",
    "Aap kaun hain aur aap kya kar sakti hain?",
    "Tumhari capabilities kya hain?",
    "آپ کون ہیں؟",
    "تم کیا کر سکتی ہو؟",
])
def test_self_description_questions_get_the_full_block(prompt):
    assert wants_runtime_detail(prompt), prompt


@pytest.mark.parametrize("prompt", [
    "Summarise my billing notes from last week.",
    "Remind me why we chose PostgreSQL for the billing service.",
    "How are you today?",
    "Tell me a joke about databases.",
    "What is the weather like in Karachi?",
    "Write a function that reverses a list.",
    "Billing service ke liye hum ne kya faisla kiya tha?",
    "بلنگ سروس کے لیے ہم نے کیا فیصلہ کیا تھا",
    "",
])
def test_ordinary_turns_get_the_compact_block(prompt):
    assert not wants_runtime_detail(prompt), prompt


def test_non_string_prompt_is_treated_as_ordinary():
    assert not wants_runtime_detail(None)  # type: ignore[arg-type]
    assert not wants_runtime_detail(123)  # type: ignore[arg-type]


# ------------------------------------------------------------------ end to end


def _system_prompt(tmp_path, monkeypatch, prompt):
    provider = Scripted("local", reply="ok")
    client, _memory, _ = client_for(tmp_path, monkeypatch, provider)
    assert chat(client, prompt, conversation_id="budget-chat-0001").status_code == 200
    return provider.seen[0].messages[0]["content"]


def test_ordinary_chat_gets_a_compact_block_and_full_chat_gets_details(tmp_path, monkeypatch):
    ordinary = _system_prompt(tmp_path / "a", monkeypatch, "Summarise my billing notes.")
    asked = _system_prompt(tmp_path / "b", monkeypatch, "What can you do and which tools do you have?")

    assert "LIVE RUNTIME EVIDENCE" in ordinary and "SPS layer responsibilities:" not in ordinary
    assert "SPS layer responsibilities:" in asked
    assert len(ordinary) < len(asked) - 1000
    for text in (ordinary, asked):  # honesty rules are present either way
        assert "EVIDENCE LIMITS" in text and "SELF-DESCRIPTION RULE" in text
    assert persona_of(ordinary) == persona_of(asked)  # the persona never varies with the turn


def test_the_default_system_prompt_stays_within_a_fixed_budget(tmp_path, monkeypatch):
    ordinary = _system_prompt(tmp_path, monkeypatch, "Summarise my billing notes.")
    assert len(ordinary) <= 2800, len(ordinary)  # was about 4100 before this change


async def test_brain_passes_the_prompt_only_to_identity_callables_that_accept_it(tmp_path):
    from mia.agent.tools import ToolRegistry
    from mia.brain.orchestrator import Brain, TurnRequest
    from mia.gateway.registry import ProviderRegistry
    from mia.gateway.service import ModelGateway
    from mia.memory.store import MemoryStore

    def make(identity):
        registry = ProviderRegistry()
        registry.register(Scripted("local", reply="ok"))
        return Brain(gateway=ModelGateway(registry), memory=MemoryStore(tmp_path / "m.sqlite3"),
                     tools=ToolRegistry(), identity=identity)

    received = []

    def with_prompt(prompt=None):
        received.append(prompt)
        return "You are Mia.", 1

    zero_arg_calls = []

    def zero_arg():
        zero_arg_calls.append(True)
        return "You are Mia.", 1

    request = TurnRequest("What can you do?", conversation_id="identity-hook-001", use_tools=False)
    assert (await make(with_prompt).run_turn(request))["content"]
    assert received == ["What can you do?"]
    assert (await make(zero_arg).run_turn(request))["content"]  # still supported
    assert zero_arg_calls == [True]
