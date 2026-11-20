import json
from pathlib import Path

from mia.brain.self_description import build_runtime_manifest, render_runtime_manifest
from mia.memory.context import assemble_memory_context, detect_context_intent
from mia.memory.store import MemoryStore, extract_query_terms

FIXTURE = Path(__file__).parent / "fixtures" / "conversation_eval.json"


def test_multilingual_conversation_evaluation_set(tmp_path):
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    store = MemoryStore(tmp_path / "memory.sqlite3")
    store.structured.add(
        record_type="decision",
        title="Billing database decision",
        body="We decided to use PostgreSQL for the billing service for transactional safety.",
        provenance="owner_provided",
        confidence=0.96,
        evidence=[{"source": "owner_conversation", "turn": "architecture-review"}],
        details={"rationale": "Transactional integrity and mature operations"},
        origin={"source": "conversation_eval_seed"},
    )
    conversation = store.ensure_conversation("conversation-eval")

    for case in cases:
        if "expected_intent" in case:
            prompt = case["prompt"]
            assert case["expected_intent"] in detect_context_intent(prompt), case["id"]
            packet = assemble_memory_context(store, prompt, conversation)
            assert not packet.empty, case["id"]
            assert case["expected_memory"] in packet.message["content"], case["id"]
            assert any(ref.title == "Billing database decision" for ref in packet.references), case["id"]

    roman_terms = extract_query_terms("Billing service ke liye hum ne kya faisla kiya tha")
    assert {"billing", "service", "faisla"} <= set(roman_terms)
    urdu_terms = extract_query_terms("بلنگ سروس کے لیے ہم نے کیا فیصلہ کیا تھا")
    assert {"بلنگ", "سروس", "فیصلہ"} <= set(urdu_terms)

    manifest_case = next(case for case in cases if case["id"] == "runtime-self-description")
    manifest = build_runtime_manifest(
        tool_names=["memory.search", "browser.open"],
        provider_ids=["ollama"],
        route_paths=["/api/chat", "/api/voice/transcribe"],
        memory_enabled=True,
        semantic_memory_enabled=False,
        permission_broker_enabled=True,
        capability_registry_enabled=True,
    )
    rendered = render_runtime_manifest(manifest)
    assert manifest_case["expected_manifest_phrase"] in rendered
