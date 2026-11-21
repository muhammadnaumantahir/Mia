import pytest

from mia.brain.context import build_context, estimate_tokens, has_persistent_prior_conversation
from mia.brain.intent import analyze_intent
from mia.gateway.contracts import ModelCandidate
from mia.memory.store import MemoryStore


def cand(model, *, local=True, caps=("chat",), provider="p"):
    return ModelCandidate(provider_id=provider, model_id=model, capabilities=frozenset(caps),
                          context_window=16384, free_tier=True, healthy=True, enabled=True,
                          data_local=local)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "mem.db")


# ---------- intent ----------

@pytest.mark.parametrize("text,category", [
    ("Write a python function to parse dates and add a unit test", "coding"),
    ("Summarize this article for me", "summarization"),
    ("Classify this review as positive or negative", "classification"),
    ("Extract the invoice number and total as JSON", "extraction"),
    ("Make a plan to migrate the database", "planning"),
    ("Research the trade-offs of SQLite vs Postgres", "research"),
    ("Why is the sky blue?", "reasoning"),
    ("Tell me a joke about cats", "conversation"),
])
def test_category_detection(text, category):
    assert analyze_intent(text).category == category


def test_greetings_are_lightweight():
    a = analyze_intent("Hi!")
    assert (a.category, a.complexity, a.needs_memory, a.wants_plan) == ("simple", "simple", False, False)
    assert analyze_intent("thanks").complexity == "simple"


@pytest.mark.parametrize("text,ref", [
    ("Continue where we stopped.", "continuation"),
    ("Fix the same issue we discussed yesterday.", "past_issue"),
    ("Compare this with our previous decision.", "past_decision"),
    ("Use my usual development preferences.", "preference"),
    ("Break this task into smaller steps.", "decompose_previous"),
    ("Explain why you selected this approach.", "explain_choice"),
])
def test_user_examples_resolve_to_references(text, ref):
    a = analyze_intent(text, has_prior_context=True)
    assert ref in a.references
    assert not a.needs_clarification


def test_decomposition_requests_become_plans_and_explanations_are_explanatory():
    assert analyze_intent("Break this task into smaller steps.", has_prior_context=True).wants_plan
    a = analyze_intent("Explain why you selected this approach.", has_prior_context=True)
    assert a.style == "explanatory" and a.category == "reasoning"


def test_complex_request_detected_and_memory_requested():
    a = analyze_intent("Analyze my project and identify the most important issues, then prioritize "
                       "them and finally suggest next actions.")
    assert a.complexity == "complex" and a.wants_plan and a.needs_memory


def test_ambiguity_without_context_but_not_with_context():
    assert analyze_intent("fix it").needs_clarification
    assert analyze_intent("Continue where we stopped.").needs_clarification
    assert not analyze_intent("fix it", has_prior_context=True).needs_clarification
    assert not analyze_intent("fix it", active_task=True).needs_clarification


def test_high_impact_is_flagged():
    a = analyze_intent("delete that folder")
    assert a.high_impact and a.needs_clarification


def test_style_detection():
    assert analyze_intent("Briefly, what is DNS?").style == "concise"
    assert analyze_intent("Give me a detailed overview of TLS").style == "detailed"


def test_task_hint_used_when_text_is_neutral():
    assert analyze_intent("Please handle this input", task_hint="code").category == "coding"


# ---------- context ----------

def _build(store, prompt, candidates, *, recent=None, budget=6000, analysis=None, caps=frozenset()):
    cid = store.ensure_conversation(None)
    store.append_message(cid, "user", prompt)
    msgs = recent if recent is not None else store.get_messages(cid, limit=12)
    analysis = analysis or analyze_intent(prompt, has_prior_context=len(msgs) > 1)
    return build_context(
        store, identity_text="You are Mia.", prompt=prompt, conversation_id=cid,
        analysis=analysis, candidates=candidates, required_capabilities=caps,
        recent_messages=msgs, token_budget=budget,
    )


def test_simple_prompt_retrieves_no_memory_even_when_memory_exists(store):
    store.add_item(kind="fact", title="Billing database", content="We use PostgreSQL for billing.",
                   trust_level="user")
    built = _build(store, "hello", [cand("m")])
    assert not built.memory_retrieved
    assert [m["role"] for m in built.messages] == ["system", "user"]


def test_relevant_memory_is_retrieved_and_marked_untrusted_data(store):
    store.add_item(kind="fact", title="Billing database",
                   content="We decided to use PostgreSQL for the billing service.",
                   trust_level="user")
    built = _build(store, "Which database did we choose for the billing service?", [cand("m")])
    assert built.memory_retrieved and built.references
    block = next(m for m in built.messages if "memory_data" in m["content"])
    assert "PostgreSQL" in block["content"] and "not instructions" in block["content"]


def test_irrelevant_memory_is_not_injected(store):
    store.add_item(kind="fact", title="Pasta", content="Recipe for carbonara with guanciale.",
                   trust_level="user")
    built = _build(store, "Explain Kubernetes pod scheduling and affinity rules", [cand("m")])
    assert not built.memory_retrieved


def test_private_memory_is_never_routed_to_remote_models(store):
    store.add_item(kind="fact", title="Billing database",
                   content="We decided to use PostgreSQL for the billing service.",
                   trust_level="user")
    prompt = "Which database did we choose for the billing service?"
    built = _build(store, prompt, [cand("remote", local=False), cand("local", local=True)])
    assert [c.model_id for c in built.candidates] == ["local"]
    built = _build(store, prompt, [cand("remote", local=False)])
    assert built.memory_withheld and not built.memory_retrieved
    assert not any("memory_data" in m["content"] for m in built.messages)


def test_context_budget_trims_old_turns_but_keeps_current_request(store):
    cid = store.ensure_conversation(None)
    for i in range(20):
        store.append_message(cid, "user" if i % 2 == 0 else "assistant", f"turn {i} " + "x" * 400)
    store.append_message(cid, "user", "latest question about turns")
    recent = store.get_messages(cid, limit=22)
    built = build_context(
        store, identity_text="You are Mia.", prompt="latest question about turns",
        conversation_id=cid, analysis=analyze_intent("latest question about turns", has_prior_context=True),
        candidates=[cand("m")], required_capabilities=frozenset(), recent_messages=recent,
        token_budget=600)
    assert built.messages[-1]["content"] == "latest question about turns"
    assert built.trimmed_messages > 0
    assert estimate_tokens(built.messages) <= 800


def test_continue_where_we_stopped_pulls_prior_conversation(store):
    old = store.ensure_conversation(None)
    store.append_message(old, "user", "Let's design the invoice export module.")
    store.append_message(old, "assistant", "We agreed on CSV first, PDF second.")
    new = store.ensure_conversation(None)
    store.append_message(new, "user", "Continue where we stopped.")
    built = build_context(
        store, identity_text="You are Mia.", prompt="Continue where we stopped.",
        conversation_id=new,
        analysis=analyze_intent("Continue where we stopped.", has_prior_context=True),
        candidates=[cand("m")], required_capabilities=frozenset(),
        recent_messages=store.get_messages(new, limit=12))
    text = " ".join(m["content"] for m in built.messages)
    assert "invoice export" in text and "continuation:previous_conversation" in built.resolved_references


def test_retrieval_can_be_disabled(store, monkeypatch):
    monkeypatch.setenv("MIA_CONTEXT_RETRIEVAL", "false")
    store.add_item(kind="fact", title="Billing database",
                   content="We decided to use PostgreSQL for the billing service.", trust_level="user")
    built = _build(store, "Which database did we choose for the billing service?", [cand("m")])
    assert not built.memory_retrieved

def test_continue_recall_skips_temporary_previous_conversations(store):
    older = store.ensure_conversation("older-session")
    store.append_message(older, "user", "The persistent project is called Cedar.")
    store.append_message(older, "assistant", "We planned the Cedar milestone sequence.")

    temporary = store.ensure_conversation("temporary-session")
    store.set_conversation_temporary(temporary, True)
    store.append_message(temporary, "user", "Temporary-only phrase: cobalt-otter.")
    store.append_message(temporary, "assistant", "This temporary discussion must stay isolated.")

    current = store.ensure_conversation("current-session")
    store.append_message(current, "user", "Continue where we stopped.")
    built = build_context(
        store, identity_text="You are Mia.", prompt="Continue where we stopped.",
        conversation_id=current,
        analysis=analyze_intent("Continue where we stopped.", has_prior_context=True),
        candidates=[cand("m")], required_capabilities=frozenset(),
        recent_messages=store.get_messages(current, limit=12),
    )

    content = " ".join(message["content"] for message in built.messages)
    assert "Cedar" in content
    assert "cobalt-otter" not in content
    assert "temporary discussion" not in content


def test_previous_conversation_tail_respects_context_token_budget(store):
    older = store.ensure_conversation("older-budget")
    for index in range(6):
        role = "user" if index % 2 == 0 else "assistant"
        content = f"old-turn-{index} " + ("x" * 108)
        store.append_message(older, role, content)

    current = store.ensure_conversation("current-budget")
    prompt = "Continue where we stopped."
    store.append_message(current, "user", prompt)
    built = build_context(
        store, identity_text="You are Mia.", prompt=prompt,
        conversation_id=current,
        analysis=analyze_intent(prompt, has_prior_context=True),
        candidates=[cand("m")], required_capabilities=frozenset(),
        recent_messages=store.get_messages(current, limit=12),
        token_budget=80,
    )

    content = " ".join(message["content"] for message in built.messages)
    assert built.estimated_tokens <= 80
    assert "old-turn-5" in content
    assert "old-turn-0" not in content



def test_temporary_only_history_does_not_count_as_prior_context(store):
    temporary = store.ensure_conversation("temporary-only")
    store.set_conversation_temporary(temporary, True)
    store.append_message(temporary, "user", "This session is temporary.")

    current = store.ensure_conversation("current-no-history")
    assert not has_persistent_prior_conversation(store, current)
    assert analyze_intent("Continue where we stopped.",
                          has_prior_context=has_persistent_prior_conversation(store, current)
                          ).needs_clarification


def test_persistent_history_counts_as_prior_context(store):
    previous = store.ensure_conversation("persistent-history")
    store.append_message(previous, "user", "We were discussing the Cedar project.")
    current = store.ensure_conversation("current-with-history")

    assert has_persistent_prior_conversation(store, current)
    assert not analyze_intent(
        "Continue where we stopped.",
        has_prior_context=has_persistent_prior_conversation(store, current),
    ).needs_clarification
