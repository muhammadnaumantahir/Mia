import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, StreamChunk, TokenUsage
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.context import (
    ContextBudget,
    assemble_memory_context,
    detect_context_intent,
)
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def make(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    return store, store.ensure_conversation("conv-retrieval-1")


def add(store, record_type, title, body, **kw):
    return store.structured.add(record_type=record_type, title=title, body=body, **kw)[0]


def ids(packet):
    return [ref.ref_id for ref in packet.references]


def text(packet):
    return packet.message["content"] if packet.message else ""


# ------------------------------------------------------- contextual references


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Continue my previous project.", {"project", "goal", "decision"}),
        ("What did we decide last time?", {"decision"}),
        ("What is the current state of this feature?", {"project", "goal", "decision"}),
        ("Use my usual preferences.", {"preference"}),
        ("What was the problem we encountered earlier?", {"lesson", "episode", "project"}),
        ("What are my goals?", {"goal"}),
        ("Translate this sentence into French", set()),
    ],
)
def test_contextual_reference_intents_are_detected(prompt, expected):
    assert set(detect_context_intent(prompt)) == expected


def test_what_did_we_decide_finds_decisions_without_any_shared_keyword(tmp_path):
    store, conv = make(tmp_path)
    decision = add(store, "decision", "Billing database", "Use PostgreSQL for invoices",
                   details={"rationale": "strong transactional guarantees"})
    add(store, "preference", "Answer style", "Short answers with code first")
    packet = assemble_memory_context(store, "What did we decide last time?", conv)
    assert ids(packet) == [decision]
    assert "PostgreSQL" in text(packet) and "strong transactional" in text(packet)
    assert "Short answers" not in text(packet)  # wrong type for this question


def test_usual_preferences_return_preferences_and_ignore_other_types(tmp_path):
    store, conv = make(tmp_path)
    pref = add(store, "preference", "Answer style", "Short answers with code first")
    add(store, "decision", "Billing database", "Use PostgreSQL for invoices")
    assert ids(assemble_memory_context(store, "Use my usual preferences.", conv)) == [pref]


def test_continue_previous_project_returns_projects_goals_and_decisions(tmp_path):
    store, conv = make(tmp_path)
    project = add(store, "project", "Mia memory", "Persistent memory feature in progress")
    goal = add(store, "goal", "Ship beta", "Ship the beta in March")
    add(store, "preference", "Answer style", "Short answers")
    assert set(ids(assemble_memory_context(store, "Continue my previous project.", conv))) == {
        project, goal,
    }


def test_the_problem_we_hit_earlier_returns_lessons_and_episodes(tmp_path):
    store, conv = make(tmp_path)
    lesson = add(store, "lesson", "Pin versions", "Unpinned dependencies broke the build")
    episode = add(store, "episode", "Failed deploy", "The deploy failed on Friday night")
    add(store, "preference", "Answer style", "Short answers")
    assert set(ids(assemble_memory_context(
        store, "What was the problem we encountered earlier?", conv))) == {lesson, episode}


def test_intent_retrieval_skips_everything_that_is_not_currently_valid(tmp_path):
    store, conv = make(tmp_path)
    live = add(store, "decision", "Live decision", "Use PostgreSQL")
    for status_change in ("archive", "mark_outdated"):
        record = add(store, "decision", f"Other {status_change}", f"Option {status_change} chosen")
        getattr(store.structured, status_change)(record)
    add(store, "decision", "Pending one", "A pending suggestion text", status="pending",
        provenance="observed")
    add(store, "decision", "Temporary one", "Short lived choice", temporary=True,
        expires_at="2000-01-01T00:00:00+00:00")
    rejected = add(store, "decision", "Rejected one", "Rejected suggestion text", status="pending",
                   provenance="observed")
    store.structured.set_status(rejected, "rejected")
    assert ids(assemble_memory_context(store, "What did we decide last time?", conv)) == [live]


def test_generic_prompt_with_no_stored_memory_returns_nothing(tmp_path):
    store, conv = make(tmp_path)
    assert assemble_memory_context(store, "What did we decide last time?", conv).empty
    assert assemble_memory_context(store, "Translate this into French", conv).empty


# ------------------------------------------------------------- settings gates


def test_memory_and_retrieval_switches_disable_retrieval_entirely(tmp_path):
    store, conv = make(tmp_path)
    add(store, "decision", "Billing database", "Use PostgreSQL for invoices")
    prompt = "What did we decide about the billing database?"
    assert not assemble_memory_context(store, prompt, conv).empty
    store.settings.update({"retrieval_enabled": False})
    assert assemble_memory_context(store, prompt, conv).empty
    store.settings.update({"retrieval_enabled": True, "memory_enabled": False})
    assert assemble_memory_context(store, prompt, conv).empty
    store.settings.update({"memory_enabled": True})
    assert not assemble_memory_context(store, prompt, conv).empty


# ------------------------------------------------------ ranking and weighting


@pytest.mark.parametrize("high_created_first", [True, False])
def test_importance_orders_otherwise_similar_records(tmp_path, high_created_first):
    store, conv = make(tmp_path)
    specs = [
        ("high", "Deploy note B", "The deploy window closes on Tuesday evening", 0.95),
        ("low", "Deploy note A", "The deploy window opens on Tuesday morning", 0.1),
    ]
    if not high_created_first:
        specs.reverse()
    created = {name: add(store, "fact", title, body, importance=importance)
               for name, title, body, importance in specs}
    order = ids(assemble_memory_context(store, "deploy window Tuesday", conv))
    assert order.index(created["high"]) < order.index(created["low"])


@pytest.mark.parametrize("sure_created_first", [True, False])
def test_low_confidence_ranks_below_high_confidence(tmp_path, sure_created_first):
    store, conv = make(tmp_path)
    specs = [
        ("sure", "Release day plan", "The release day is Thursday morning", 0.95),
        ("shaky", "Release day guess", "The release day is Thursday afternoon", 0.2),
    ]
    if not sure_created_first:
        specs.reverse()
    created = {
        name: add(store, "fact", title, body, provenance="inferred", confidence=confidence,
                  evidence=[{"type": "message", "ref": f"m-{name}"}])
        for name, title, body, confidence in specs
    }
    order = ids(assemble_memory_context(store, "release day Thursday", conv))
    assert order.index(created["sure"]) < order.index(created["shaky"])


def test_owner_confirmed_outranks_unverified_and_labels_say_so(tmp_path):
    store, conv = make(tmp_path)
    guess = add(store, "fact", "Editor guess", "The owner edits code with Emacs daily",
                provenance="inferred", evidence=[{"type": "message", "ref": "m1"}])
    stated = add(store, "fact", "Editor stated", "The owner edits code with Neovim daily")
    packet = assemble_memory_context(store, "which editor does the owner use for code", conv)
    order = ids(packet)
    assert order.index(stated) < order.index(guess)
    assert "owner-provided" in text(packet) and "inference (unverified)" in text(packet)


def test_duplicates_collapse_and_the_budget_caps_what_is_injected(tmp_path):
    store, conv = make(tmp_path)
    for index in range(12):
        add(store, "fact", f"Server note {index}", f"The server rack number {index} hosts services")
    packet = assemble_memory_context(store, "server rack hosts services", conv)
    assert len(packet.references) == ContextBudget().max_records
    assert len(text(packet)) < ContextBudget().max_chars + 1500
    store2 = MemoryStore(tmp_path / "other.sqlite3")
    conv2 = store2.ensure_conversation("conv-retrieval-2")
    for index in range(3):
        store2.add_item(kind="fact", title=f"Dup {index}", content="The vault door code word is walrus",
                        trust_level="user")
    assert len(assemble_memory_context(store2, "vault door code word", conv2).references) == 1


def test_edited_corrected_and_deleted_memories_change_what_is_retrieved(tmp_path):
    store, conv = make(tmp_path)
    record = add(store, "preference", "Answer style", "Always answer in long detailed essays")
    prompt = "How should answers be written, what is my answer style?"
    assert "essays" in text(assemble_memory_context(store, prompt, conv))
    store.structured.edit(record, body="Always answer in two short sentences")
    after_edit = text(assemble_memory_context(store, prompt, conv))
    assert "two short sentences" in after_edit and "essays" not in after_edit
    store.structured.delete(record)
    assert assemble_memory_context(store, prompt, conv).empty


# ---------------------------------------------------------------- relationships


def test_linked_records_are_followed_but_private_and_unavailable_ones_are_not(tmp_path):
    store, conv = make(tmp_path)
    project = add(store, "project", "Zephyr rollout", "Zephyr rollout project for the new gateway")
    decision = add(store, "decision", "Gateway vendor", "Choose the Acme gateway vendor")
    secret = add(store, "decision", "Private pricing", "Negotiated private pricing terms")
    archived = add(store, "decision", "Old vendor", "Dropped the Legacy gateway vendor")
    store.relationships.add(decision, project, "part_of")
    store.relationships.add(secret, project, "part_of", visibility="private")
    store.relationships.add(archived, project, "part_of")
    store.structured.archive(archived)
    packet = assemble_memory_context(store, "Zephyr rollout status", conv)
    assert project in ids(packet) and decision in ids(packet)
    assert secret not in ids(packet) and archived not in ids(packet)
    assert "linked (part_of)" in text(packet)
    assert "Negotiated" not in text(packet) and "Legacy" not in text(packet)


# --------------------------------------------------------------- optional rerank


def test_optional_semantic_reranker_can_promote_a_record_and_failure_is_harmless(tmp_path):
    store, conv = make(tmp_path)
    first = add(store, "fact", "Cache setting one", "Cache layer uses Redis with short expiry")
    second = add(store, "fact", "Cache setting two", "Cache layer uses Memcached with long expiry")
    baseline = ids(assemble_memory_context(store, "cache layer expiry", conv))
    promote = baseline[-1]
    boosted = ids(assemble_memory_context(
        store, "cache layer expiry", conv, reranker=lambda _q, _r: {promote: 0.3}
    ))
    assert boosted[0] == promote and set(boosted) == {first, second}

    def broken(_query, _records):
        raise RuntimeError("embedding service unavailable")

    assert ids(assemble_memory_context(store, "cache layer expiry", conv, reranker=broken)) == baseline


# ------------------------------------------------------------------ performance


def test_retrieval_stays_bounded_and_fast_with_a_large_memory(tmp_path):
    store, conv = make(tmp_path)
    for index in range(1500):
        add(store, "fact", f"Generic fact {index}", f"Unrelated note number {index} about topic{index % 50}")
    target = add(store, "decision", "Quarterly audit schedule", "Audit happens every quarter on the first Monday")
    started = time.perf_counter()
    packet = assemble_memory_context(store, "when is the quarterly audit schedule", conv)
    elapsed = time.perf_counter() - started
    assert target in ids(packet) and len(packet.references) <= 6
    assert elapsed < 2.0, f"retrieval took {elapsed:.2f}s"


# --------------------------------------------------------------- chat + provider


class RecordingProvider:
    def __init__(self, provider_id="local-a", model_id="model-alpha"):
        self.provider_id, self.model_id = provider_id, model_id
        self.requests = []

    async def health(self):
        return True

    async def list_models(self):
        return [ModelCandidate(
            provider_id=self.provider_id, model_id=self.model_id,
            capabilities=frozenset({"chat"}), context_window=8192, free_tier=True,
            healthy=True, enabled=True, data_local=True,
        )]

    async def complete(self, candidate, request):
        self.requests.append(request)
        return "ok"

    async def stream(self, candidate, request):
        self.requests.append(request)
        yield StreamChunk(delta="ok")
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(3, 1))

    def memory_block(self):
        for message in self.requests[-1].messages:
            if message["role"] == "system" and "<memory_data>" in message["content"]:
                return message["content"]
        return ""


def make_client(tmp_path, monkeypatch, provider=None, memory=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    provider = provider or RecordingProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    memory = memory or MemoryStore(tmp_path / "memory.sqlite3")
    obs = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(create_app(store=obs, gateway=ModelGateway(registry), memory=memory))
    return client, memory, provider


def chat(client, prompt, conversation_id, stream=False):
    return client.post(
        "/api/gateway/chat/stream" if stream else "/api/gateway/chat",
        json={"prompt": prompt, "conversation_id": conversation_id, "use_tools": False},
    )


def test_memory_reaches_the_model_with_sources_and_survives_restart_and_provider_switch(
    tmp_path, monkeypatch
):
    client_a, memory, provider_a = make_client(tmp_path, monkeypatch, RecordingProvider())
    add(memory, "decision", "Billing database", "Use PostgreSQL for invoices")
    reply = chat(client_a, "What did we decide last time?", "conv-aaaa-1111").json()
    block_a = provider_a.memory_block()
    assert "PostgreSQL" in block_a and "owner-provided" in block_a
    assert reply["context_references"][0]["title"] == "Billing database"

    # a new process, a different provider and model, the same database
    client_b, _, provider_b = make_client(
        tmp_path, monkeypatch, RecordingProvider("local-b", "model-beta"),
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
    )
    chat(client_b, "What did we decide last time?", "conv-bbbb-2222")
    block_b = provider_b.memory_block()

    def decision_section(block):
        match = re.search(r"\[M\d+\] decision.*?(?=\n\n|\n</memory_data>)", block, re.DOTALL)
        assert match, block
        return re.sub(r"\[M\d+\]", "[M]", match.group(0))

    # the stored decision renders identically whichever provider or model answers
    assert decision_section(block_b) == decision_section(block_a)
    for block in (block_a, block_b):
        assert "model-alpha" not in block and "model-beta" not in block


def test_streaming_path_gets_the_same_memory(tmp_path, monkeypatch):
    client, memory, provider = make_client(tmp_path, monkeypatch)
    add(memory, "preference", "Answer style", "Short answers with code first")
    response = chat(client, "Use my usual preferences.", "conv-aaaa-1111", stream=True)
    assert response.status_code == 200 and "reply_id" in response.text
    assert "Short answers" in provider.memory_block()


def test_each_reply_records_what_memory_it_used_and_deletion_removes_the_trace(
    tmp_path, monkeypatch
):
    client, memory, _ = make_client(tmp_path, monkeypatch)
    decision = add(memory, "decision", "Billing database", "Use PostgreSQL for invoices")
    reply = chat(client, "What did we decide last time?", "conv-aaaa-1111").json()
    used = client.get(f"/api/memory/replies/{reply['reply_id']}/usage").json()["used"]
    assert [(u["ref_type"], u["ref_id"]) for u in used] == [("record", decision)]
    detail = client.get(f"/api/memory/records/{decision}").json()
    assert detail["used_in_replies"][0]["reply_id"] == reply["reply_id"]
    assert memory.structured.get(decision)["use_count"] == 1
    assert memory.structured.get(decision)["last_used_at"]

    other = chat(client, "Hello, how are you today?", "conv-aaaa-1111").json()
    assert client.get(f"/api/memory/replies/{other['reply_id']}/usage").json()["used"] == []

    client.delete(f"/api/memory/records/{decision}")
    assert client.get(f"/api/memory/replies/{reply['reply_id']}/usage").json()["used"] == []


def test_chat_with_memory_off_sends_no_memory_and_still_works(tmp_path, monkeypatch):
    client, memory, provider = make_client(tmp_path, monkeypatch)
    add(memory, "decision", "Billing database", "Use PostgreSQL for invoices")
    client.put("/api/memory/settings", json={"retrieval_enabled": False})
    response = chat(client, "What did we decide last time?", "conv-aaaa-1111")
    assert response.status_code == 200 and response.json()["context_references"] == []
    assert provider.memory_block() == ""


def test_usage_logging_failure_never_loses_the_reply(tmp_path, monkeypatch):
    client, memory, _ = make_client(tmp_path, monkeypatch)
    add(memory, "decision", "Billing database", "Use PostgreSQL for invoices")

    def broken(**_kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(memory.usage, "record", broken)
    response = chat(client, "What did we decide last time?", "conv-aaaa-1111")
    assert response.status_code == 200 and response.json()["content"] == "ok"


def test_deleted_memory_is_not_used_in_later_replies_anywhere(tmp_path, monkeypatch):
    client, memory, provider = make_client(tmp_path, monkeypatch)
    saved = chat(client, "Remember that my project codename is Falcon.", "conv-aaaa-1111").json()
    record_id = saved["memory_capture"]["saved"][0]["record_id"]
    chat(client, "What is my project codename?", "conv-bbbb-2222")
    assert "Falcon" in provider.memory_block()
    client.delete(f"/api/memory/records/{record_id}")
    for conversation in ("conv-bbbb-2222", "conv-cccc-3333"):
        chat(client, "What is my project codename?", conversation)
        assert "Falcon" not in provider.memory_block()
        assert "Falcon" not in json.dumps([m["content"] for m in provider.requests[-1].messages
                                           if m["role"] != "user"])
