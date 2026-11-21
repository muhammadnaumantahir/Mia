"""MIA-GW-03: language-aware routing hint. Deterministic; proves ordering behaviour only,
not the real Urdu quality of any specific model."""

import pytest

from mia.brain.router import BrainRouter, RoutingPreferences
from mia.gateway.contracts import ModelCandidate, ModelRequest
from mia.gateway.language import detect_language


@pytest.mark.parametrize("text,expected", [
    ("What is the capital of France?", "en"),
    ("", "en"),
    ("12345 !!!", "en"),
    ("آپ کیسے ہیں؟ مجھے مدد چاہیے", "urdu_script"),
    ("kya haal hai, mujhe madad chahiye", "roman_urdu"),
    ("Aap kaise hain? Mera kaam abhi karo", "roman_urdu"),
    # guards: English that merely contains a stray word must stay English
    ("Please do it to me so we can see what happens next week.", "en"),
    ("I want to read a long article about the hai-ku poem form and how it works in practice", "en"),
    ("Let's debug this Python function that returns None for empty lists", "en"),
])
def test_detect_language(text, expected):
    assert detect_language(text) == expected


def cand(model, *, local=True, latency=1000.0):
    return ModelCandidate("p", model, frozenset({"chat"}), 16384, True, True, True,
                          reliability_score=0.5, latency_ms=latency, data_local=local)


def req(text):
    return ModelRequest(task_type="simple", prompt=text, estimated_input_tokens=50, max_output_tokens=200,
                        required_capabilities=frozenset({"chat"}))


TINY, MID, UNKNOWN = cand("tiny-1b", latency=200.0), cand("mid-8b", latency=1500.0), cand("mystery", latency=300.0)


def order(text, candidates, task="simple", prefs=RoutingPreferences()):
    d = BrainRouter().route(list(candidates), req(text), task_type=task, preferences=prefs)
    return [c.model_id for c in d.ordered], d


def test_english_simple_turn_still_prefers_the_small_fast_model():
    assert order("Hi, how are you?", [TINY, MID])[0][0] == "tiny-1b"


@pytest.mark.parametrize("text", ["آپ کیسے ہیں؟ مجھے مدد چاہیے", "kya haal hai, mujhe madad chahiye"])
def test_urdu_and_roman_urdu_simple_turns_prefer_the_larger_model(text):
    ids, decision = order(text, [TINY, MID])
    assert ids[0] == "mid-8b"
    assert any("larger model preferred for" in r for r in decision.reasons)  # the routing audit explains why


def test_small_model_is_never_excluded_only_reordered():
    ids, _ = order("kya haal hai, mujhe madad chahiye", [TINY])
    assert ids == ["tiny-1b"]  # local-only / single-model setups keep working


def test_unknown_size_models_are_not_penalised():
    ids, _ = order("kya haal hai, mujhe madad chahiye", [TINY, UNKNOWN])
    assert ids[0] == "mystery"


def test_structured_task_categories_are_unaffected():
    ids, _ = order("kya haal hai, mujhe madad chahiye", [TINY, MID], task="classification")
    assert ids[0] == "tiny-1b"


def test_user_preference_still_wins_over_the_language_hint():
    prefs = RoutingPreferences(preferred_models=("tiny-1b",))
    assert order("kya haal hai, mujhe madad chahiye", [TINY, MID], prefs=prefs)[0][0] == "tiny-1b"
