import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest, StreamChunk, TokenUsage
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.capture import CaptureService, classify, similarity
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


def make(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite3")
    return store, CaptureService(store), store.ensure_conversation("conv-capture-1")


def say(store, service, conversation, text):
    message_id = store.append_message(conversation, "user", text)
    return service.process_message(conversation, message_id, text)


def sql(store, query, params=()):
    db = sqlite3.connect(store.database_path)
    try:
        return db.execute(query, params).fetchall()
    finally:
        db.close()


def records(store, **kw):
    return store.structured.list(limit=200, **kw)


# --------------------------------------------------------------- explicit commands


def test_explicit_remember_saves_active_owner_provided_with_provenance(tmp_path):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, "Remember that we decided to use PostgreSQL for billing.")
    assert result["explicit"] and len(result["saved"]) == 1
    record = store.structured.get(result["saved"][0]["record_id"])
    assert record["status"] == "active" and record["provenance"] == "owner_provided"
    assert record["record_type"] == "decision"
    assert record["origin"]["conversation_id"] == conv
    assert record["origin"]["capture"] == "explicit" and record["origin"]["message_id"]
    assert [r["record_id"] for r in store.retrieve_records("postgresql billing")] == [
        record["record_id"]
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Don't forget this decision: we chose Redis for caching", "decision"),
        ("Remember that my goal is to ship the beta by March", "goal"),
        ("Remember that I always review pull requests before lunch", "preference"),
        ("Remember that I learned a lesson: always pin dependency versions", "lesson"),
        ("Remember my colleague Sam reviews all releases", "entity"),
        ("Remember that the billing project requires invoicing support", "project"),
        ("Keep in mind the server room key is on the left", "fact"),
    ],
)
def test_explicit_memories_are_classified(tmp_path, text, expected):
    store, service, conv = make(tmp_path)
    assert say(store, service, conv, text)["saved"][0]["record_type"] == expected
    assert classify("The sky is blue today") == "fact"


def test_my_preferred_statement_is_an_explicit_preference(tmp_path):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, "My preferred approach is test-driven development.")
    assert result["explicit"] and result["saved"][0]["status"] == "active"
    assert result["saved"][0]["record_type"] == "preference"


def test_remember_this_uses_the_previous_user_message(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "The staging deploy password rotation happens monthly on Fridays.")
    store.append_message(conv, "assistant", "Noted, I will keep that in mind.")
    result = say(store, service, conv, "Remember this.")
    assert len(result["saved"]) == 1
    saved = store.structured.get(result["saved"][0]["record_id"])
    assert "rotation happens monthly" in saved["body"]
    assert "Noted" not in saved["body"]  # never the assistant's words


def test_remember_this_with_nothing_to_refer_to_asks_instead_of_guessing(tmp_path):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, "Remember this.")
    assert result["needs_clarification"] and records(store) == []


def test_explicit_remember_works_when_automatic_capture_is_off(tmp_path):
    store, service, conv = make(tmp_path)
    store.settings.update({"capture_mode": "off"})
    assert say(store, service, conv, "Remember that I use a standing desk.")["saved"]
    assert say(store, service, conv, "I always take notes in plain text files.")["skipped"] == [
        {"reason": "capture_off"}
    ]
    assert len(records(store)) == 1


def test_nothing_is_captured_when_memory_is_disabled_or_conversation_is_temporary(tmp_path):
    store, service, conv = make(tmp_path)
    store.settings.update({"memory_enabled": False})
    assert say(store, service, conv, "Remember that I use a standing desk.")["skipped"] == [
        {"reason": "memory_disabled"}
    ]
    store.settings.update({"memory_enabled": True})
    store.set_conversation_temporary(conv, True)
    assert say(store, service, conv, "Remember that I use a standing desk.")["skipped"] == [
        {"reason": "temporary_conversation"}
    ]
    assert say(store, service, conv, "I always take notes in plain text files.")["skipped"] == [
        {"reason": "temporary_conversation"}
    ]
    assert records(store) == []
    store.set_conversation_temporary(conv, False)
    assert say(store, service, conv, "Remember that I use a standing desk.")["saved"]


def test_explicit_credentials_are_refused_and_never_stored_or_logged(tmp_path):
    store, service, conv = make(tmp_path)
    secret = "ghp_" + "a" * 36
    result = say(store, service, conv, f"Remember that my GitHub token is {secret}")
    assert result["skipped"] == [{"reason": "credential"}] and result["saved"] == []
    assert records(store) == []
    dump = json.dumps(store.audit.list(limit=200)) + json.dumps(result)
    assert secret not in dump
    assert say(store, service, conv, "Remember that my password is hunter2hunter2")["skipped"]


# ------------------------------------------------------------- automatic capture


def test_approval_mode_queues_suggestions_that_are_never_retrieved(tmp_path):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, "I always review pull requests before lunch.")
    assert [p["status"] for p in result["pending"]] == ["pending"] and result["saved"] == []
    pending_id = result["pending"][0]["record_id"]
    record = store.structured.get(pending_id)
    assert record["provenance"] == "observed" and record["origin"]["capture"] == "auto"
    assert record["evidence"][0]["type"] == "message" and record["confidence"] == 0.7
    assert store.retrieve_records("review pull requests lunch") == []

    store.structured.approve(pending_id)
    assert [r["record_id"] for r in store.retrieve_records("review pull requests lunch")] == [
        pending_id
    ]


def test_auto_mode_saves_only_high_confidence_conflict_free_candidates(tmp_path):
    store, service, conv = make(tmp_path)
    store.settings.update({"capture_mode": "auto"})
    high = say(store, service, conv, "My favorite editor is Neovim for everything.")
    assert [s["status"] for s in high["saved"]] == ["active"]
    saved = store.structured.get(high["saved"][0]["record_id"])
    assert saved["provenance"] == "observed"  # saved, but NOT owner-confirmed
    low = say(store, service, conv, "I always review pull requests before lunch.")
    assert low["saved"] == [] and [p["status"] for p in low["pending"]] == ["pending"]


def test_auto_capture_never_overrides_an_existing_memory(tmp_path):
    store, service, conv = make(tmp_path)
    store.settings.update({"capture_mode": "auto"})
    say(store, service, conv, "Remember that my favorite editor is Neovim for everything.")
    result = say(store, service, conv, "My favorite editor is Helix for everything.")
    assert result["saved"] == [] and len(result["pending"]) == 1 and result["conflicts"]
    original = [r for r in records(store, status="active")]
    assert len(original) == 1 and "Neovim" in original[0]["body"]
    pending = store.structured.get(result["pending"][0]["record_id"])
    assert pending["origin"]["conflicts_with"] == [original[0]["record_id"]]


def test_explicit_correction_of_same_subject_supersedes_and_keeps_history(tmp_path):
    store, service, conv = make(tmp_path)
    first = say(store, service, conv, "Remember that my favorite editor is Neovim.")
    second = say(store, service, conv, "Remember that my favorite editor is Helix.")
    assert second["superseded"][0]["replaces"] == first["saved"][0]["record_id"]
    old = store.structured.get(first["saved"][0]["record_id"])
    assert old["status"] == "superseded" and "Neovim" in old["body"]
    assert [r["body"] for r in store.retrieve_records("favorite editor")][0].endswith("Helix.")
    assert len(store.structured.history(first["saved"][0]["record_id"])) == 2


def test_explicit_similar_but_different_subject_is_saved_and_flags_possible_conflict(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "Remember that the billing service uses PostgreSQL databases.")
    result = say(
        store, service, conv, "Remember that the billing service uses MySQL databases heavily."
    )
    assert result["saved"] and result["conflicts"] and result["superseded"] == []
    assert len(records(store, status="active")) == 2  # nothing silently overwritten


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Do I always review pull requests before lunch?", "question"),
        ("Maybe I always review pull requests before lunch.", "uncertain"),
        ("I think I prefer tabs over spaces.", "uncertain"),
        ("I always review pull requests right now before lunch.", "temporary_context"),
        ("I prefer working for now with the small laptop.", "temporary_context"),
        ("I always pray at home before starting work.", "sensitive_attribute"),
        ("I usually go to the mosque on Fridays after work.", "sensitive_attribute"),
        ("My goal is to recover from my diabetes diagnosis this year.", "sensitive_attribute"),
        ("I prefer the political party my family votes for.", "sensitive_attribute"),
        ("I always use the password secret=abcdefg1234 for staging.", "credential"),
    ],
)
def test_risky_or_uncertain_statements_are_not_captured_automatically(tmp_path, text, reason):
    store, service, conv = make(tmp_path)
    store.settings.update({"capture_mode": "auto"})
    result = say(store, service, conv, text)
    assert result["saved"] == [] and result["pending"] == []
    assert reason in [s["reason"] for s in result["skipped"]]
    assert records(store) == []


def test_conversational_filler_and_code_blocks_are_ignored(tmp_path):
    store, service, conv = make(tmp_path)
    store.settings.update({"capture_mode": "auto"})
    text = "Thanks, that works.\n```\nI always use sudo here\n```\nCan you explain it further?"
    assert say(store, service, conv, text)["pending"] == []
    assert records(store) == []


def test_duplicate_statements_and_retries_do_not_create_duplicates(tmp_path):
    store, service, conv = make(tmp_path)
    first = say(store, service, conv, "I always review pull requests before lunch.")
    again = say(store, service, conv, "I always review the pull requests before lunch.")
    assert again["pending"] == [] and again["skipped"][0]["reason"] == "duplicate"
    # the very same message id processed twice (a retry) is a no-op
    message_id = store.append_message(conv, "user", "Remember that my timezone is UTC+5.")
    one = service.process_message(conv, message_id, "Remember that my timezone is UTC+5.")
    two = service.process_message(conv, message_id, "Remember that my timezone is UTC+5.")
    assert one["saved"] and two["skipped"] == [{"reason": "already_processed"}]
    assert len(records(store)) == 2 and first["pending"]


def test_rejected_suggestions_are_not_suggested_again(tmp_path):
    store, service, conv = make(tmp_path)
    pending = say(store, service, conv, "I always review pull requests before lunch.")
    store.structured.set_status(pending["pending"][0]["record_id"], "rejected")
    again = say(store, service, conv, "I always review pull requests before lunch.")
    assert again["pending"] == []
    assert again["skipped"][0]["reason"] in {"previously_rejected", "duplicate"}
    assert len(records(store)) == 1
    # but an explicit instruction from the owner is honoured
    assert say(store, service, conv, "Remember that I always review pull requests before lunch.")[
        "saved"
    ]


def test_similarity_helper_basics():
    assert similarity("Uses Neovim for coding", "Uses Neovim for coding") == 1.0
    assert similarity("Uses Neovim for coding", "Likes green tea") == 0.0
    assert 0.2 < similarity("billing uses postgres", "billing uses mysql") < 0.8


# ------------------------------------------------------------------------ forget


def test_forget_deletes_the_memory_its_index_history_and_its_source_message(tmp_path):
    store, service, conv = make(tmp_path)
    saved = say(store, service, conv, "Remember that the vault codename is Falcon.")
    record_id = saved["saved"][0]["record_id"]
    other_conv = store.ensure_conversation("conv-capture-2")
    assert any("Falcon" in m["content"] for m in store.retrieve_messages("vault codename Falcon"))

    result = say(store, service, other_conv, "Forget the vault codename")
    assert result["forgotten"][0]["record_id"] == record_id
    assert result["forgotten"][0]["source_messages_removed"] == 1
    assert store.structured.get(record_id) is None
    assert store.retrieve_records("vault codename Falcon") == []
    # no stored message still carries the value (the forget command itself names only the topic)
    assert all("Falcon" not in m["content"] for m in store.retrieve_messages("vault codename Falcon"))
    assert sql(store, "SELECT COUNT(*) FROM structured_fts")[0][0] == 0
    assert "Falcon" not in json.dumps(store.audit.list(limit=200))
    assert "Falcon" not in json.dumps(sql(store, "SELECT content FROM conversation_messages"))


def test_forget_with_several_matches_asks_and_deletes_nothing(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "Remember that the billing database is PostgreSQL.")
    say(store, service, conv, "Remember that the billing cycle closes on the 25th.")
    result = say(store, service, conv, "Forget about billing")
    assert result["forgotten"] == [] and len(result["candidates"]) == 2
    assert result["needs_clarification"] and len(records(store)) == 2


def test_forget_with_no_match_or_no_subject_does_nothing(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "Remember that the billing database is PostgreSQL.")
    assert say(store, service, conv, "Forget the unicorn registry")["skipped"] == [
        {"reason": "nothing_found"}
    ]
    assert say(store, service, conv, "Forget it.")["needs_clarification"]
    assert len(records(store)) == 1


def test_forget_works_even_when_memory_is_off_or_conversation_is_temporary(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "Remember that the vault codename is Falcon.")
    store.settings.update({"memory_enabled": False})
    store.set_conversation_temporary(conv, True)
    assert say(store, service, conv, "Forget the vault codename")["forgotten"]
    assert records(store) == []


# ------------------------------------------------------------------------ update


def test_update_supersedes_a_single_match_and_preserves_the_original(tmp_path):
    store, service, conv = make(tmp_path)
    first = say(store, service, conv, "Remember that we decided the launch date is in May.")
    result = say(store, service, conv, "Update what you remember about the launch date to June 3rd")
    assert result["superseded"][0]["replaces"] == first["saved"][0]["record_id"]
    old = store.structured.get(first["saved"][0]["record_id"])
    assert old["status"] == "superseded" and "May" in old["body"]
    assert store.retrieve_records("launch date")[0]["body"] == "June 3rd"


def test_update_without_a_match_saves_it_and_ambiguous_update_asks(tmp_path):
    store, service, conv = make(tmp_path)
    created = say(store, service, conv, "Update what you remember about the garden: tomatoes won")
    assert created["saved"]
    say(store, service, conv, "Remember that the billing database is PostgreSQL.")
    say(store, service, conv, "Remember that the billing cycle closes on the 25th.")
    ambiguous = say(store, service, conv, "Update what you remember about billing to monthly")
    assert ambiguous["needs_clarification"] and ambiguous["superseded"] == []


# ---------------------------------------------------------------- chat integration


class RecordingProvider:
    provider_id = "local-test"
    model_id = "model-one"

    def __init__(self):
        self.requests: list[ModelRequest] = []

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id, model_id=self.model_id,
                capabilities=frozenset({"chat"}), context_window=8192, free_tier=True,
                healthy=True, enabled=True, data_local=True,
            )
        ]

    async def complete(self, candidate, request):
        self.requests.append(request)
        return "I prefer to answer: Noted that my favorite editor is Emacs."

    async def stream(self, candidate, request):
        self.requests.append(request)
        yield StreamChunk(delta="I always use Emacs, my favorite editor is Emacs.")
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(3, 1))

    def seen(self) -> str:
        return json.dumps([m for m in self.requests[-1].messages])


def make_client(tmp_path, monkeypatch, provider=None, memory=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    provider = provider or RecordingProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    memory = memory or MemoryStore(tmp_path / "memory.sqlite3")
    obs = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(create_app(store=obs, gateway=ModelGateway(registry), memory=memory))
    return client, memory, provider, obs


def chat(client, prompt, conversation_id=None, stream=False):
    body = {"prompt": prompt, "use_tools": False}
    if conversation_id:
        body["conversation_id"] = conversation_id
    return client.post("/api/gateway/chat/stream" if stream else "/api/gateway/chat", json=body)


def test_chat_captures_remembers_across_conversations_and_reports_the_capture(
    tmp_path, monkeypatch
):
    client, memory, provider, _ = make_client(tmp_path, monkeypatch)
    reply = chat(client, "Remember that my project codename is Falcon.", "conv-one-aaaa").json()
    assert reply["memory_capture"]["saved"][0]["record_type"] in {"project", "fact"}

    later = chat(client, "What is my project codename?", "conv-two-bbbb").json()
    assert "Falcon" in provider.seen()  # retrieved memory reached the model
    assert any(ref["ref_type"] in {"record", "memory"} for ref in later["context_references"]) or (
        later["context_references"]
    )


def test_assistant_replies_are_never_captured_as_owner_facts(tmp_path, monkeypatch):
    client, memory, _, _ = make_client(tmp_path, monkeypatch)
    memory.settings.update({"capture_mode": "auto"})
    chat(client, "Hello there, how are you doing today?", "conv-one-aaaa")
    chat(client, "Please say something about editors.", "conv-one-aaaa", stream=True)
    assert memory.structured.list(limit=50) == []  # the stub's "I always use Emacs" is ignored


def test_streaming_chat_reports_capture_in_the_done_event(tmp_path, monkeypatch):
    client, memory, _, _ = make_client(tmp_path, monkeypatch)
    response = chat(client, "Remember that I take notes in plain text.", "conv-one-aaaa", True)
    assert response.status_code == 200 and "memory_capture" in response.text
    assert len(memory.structured.list(limit=10)) == 1


def test_forget_in_chat_stops_the_fact_reaching_the_model_in_other_conversations(
    tmp_path, monkeypatch
):
    client, memory, provider, _ = make_client(tmp_path, monkeypatch)
    chat(client, "Remember that my project codename is Falcon.", "conv-one-aaaa")
    chat(client, "What is my project codename?", "conv-two-bbbb")
    assert "Falcon" in provider.seen()

    forgot = chat(client, "Forget my project codename", "conv-three-cc").json()
    assert forgot["memory_capture"]["forgotten"]
    chat(client, "What is my project codename?", "conv-four-dd")
    assert "Falcon" not in provider.seen()
    # the earlier reply text in conversation two never contained it, and neither does storage
    stored = json.dumps(sql(memory, "SELECT content FROM conversation_messages WHERE role='user'"))
    assert "Falcon" not in stored.replace("Forget my project codename", "")


def test_capture_failure_never_breaks_chat_and_is_logged(tmp_path, monkeypatch):
    client, memory, provider, obs = make_client(tmp_path, monkeypatch)

    def boom(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(memory.structured, "add", boom)
    response = chat(client, "Remember that I take notes in plain text.", "conv-one-aaaa")
    assert response.status_code == 200 and response.json()["content"]
    assert any(e["event_type"] == "capture_failure" for e in obs.list_events(limit=50))


def test_temporary_conversation_via_api_captures_nothing(tmp_path, monkeypatch):
    client, memory, _, _ = make_client(tmp_path, monkeypatch)
    chat(client, "Hello there", "conv-temp-aaaa")
    assert client.post(
        "/api/conversations/conv-temp-aaaa/temporary", json={"temporary": True}
    ).status_code == 200
    reply = chat(client, "Remember that my project codename is Falcon.", "conv-temp-aaaa").json()
    assert reply["memory_capture"]["skipped"] == [{"reason": "temporary_conversation"}]
    assert memory.structured.list(limit=10) == []


def test_capture_and_reject_endpoints(tmp_path, monkeypatch):
    client, memory, _, _ = make_client(tmp_path, monkeypatch)
    saved = client.post("/api/memory/capture", json={"text": "I use a standing desk"})
    assert saved.status_code == 201 and saved.json()["saved"][0]["status"] == "active"
    assert client.post("/api/memory/capture", json={"text": "x", "record_type": "bogus"}).status_code == 400
    assert client.post("/api/memory/capture", json={"text": "x", "extra": 1}).status_code == 422
    secret = client.post("/api/memory/capture", json={"text": "password: hunter2hunter2"})
    assert secret.json()["skipped"] == [{"reason": "credential"}]
    assert "hunter2" not in secret.text

    pending = memory.structured.add(
        record_type="fact", title="Suggestion", body="A suggested fact", status="pending",
        provenance="observed", actor="capture",
    )[0]
    assert client.post(f"/api/memory/records/{pending}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/memory/records/{pending}/reject").status_code == 409
    memory.settings.update({"memory_enabled": False})
    assert client.post("/api/memory/capture", json={"text": "I use a desk"}).status_code == 409


def test_api_delete_purges_the_source_message_unless_asked_not_to(tmp_path, monkeypatch):
    client, memory, _, _ = make_client(tmp_path, monkeypatch)
    chat(client, "Remember that the vault codename is Falcon.", "conv-one-aaaa")
    chat(client, "Remember that the garage code word is Walrus.", "conv-one-aaaa")
    falcon, walrus = sorted(memory.structured.list(limit=10), key=lambda r: r["body"])[::-1]
    assert "Walrus" in walrus["body"] or "Falcon" in walrus["body"]
    by_body = {("Falcon" in r["body"]): r for r in (falcon, walrus)}
    out = client.delete(f"/api/memory/records/{by_body[True]['record_id']}").json()
    assert out == {"deleted": True, "purged_messages": 1}
    assert memory.retrieve_messages("vault codename Falcon") == []
    kept = client.delete(
        f"/api/memory/records/{by_body[False]['record_id']}", params={"purge_source": "false"}
    ).json()
    assert kept == {"deleted": True, "purged_messages": 0}
    assert memory.retrieve_messages("garage code word Walrus")  # history intentionally kept


def test_approval_mode_keeps_even_high_confidence_suggestions_pending(tmp_path):
    store, service, conv = make(tmp_path)
    assert store.settings.get("capture_mode") == "approval"
    result = say(store, service, conv, "My favorite editor is Neovim for everything.")
    assert result["pending"][0]["confidence"] >= store.settings.get("auto_save_min_confidence")
    assert result["saved"] == []
    assert records(store, status="active") == []
    assert store.retrieve_records("favorite editor neovim") == []


def test_same_sentence_is_one_memory_whether_suggested_or_stated_explicitly(tmp_path):
    store, service, conv = make(tmp_path)
    say(store, service, conv, "I always review pull requests before lunch.")
    explicit = say(store, service, conv, "Remember that I always review pull requests before lunch.")
    assert explicit["saved"] == [] and explicit["skipped"][0]["reason"] == "duplicate"
    assert len(records(store)) == 1 and records(store)[0]["provenance"] == "owner_provided"

# ------------------------------------------------------- Urdu/Roman Urdu capture


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mujhe Roman Urdu mein baat karna pasand hai.", "preference"),
        ("Main hamesha code review karta hoon.", "preference"),
        ("Hum ne Mia ke liye SQLite use karne ka faisla kiya.", "decision"),
        ("Mera maqsad Mia ko natural conversational assistant banana hai.", "goal"),
        ("Main Mia par kaam kar raha hoon.", "project"),
        ("مجھے اردو میں بات کرنا پسند ہے۔", "preference"),
        ("میں ہمیشہ کوڈ ریویو کرتا ہوں۔", "preference"),
        ("ہم نے Mia کے لیے SQLite کا فیصلہ کیا۔", "decision"),
        ("میرا مقصد Mia کو مکمل کرنا ہے۔", "goal"),
        ("میں Mia پر کام کر رہا ہوں۔", "project"),
    ],
)
def test_automatic_capture_supports_roman_urdu_and_urdu(tmp_path, text, expected):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, text)

    assert result["saved"] == []
    assert len(result["pending"]) == 1
    assert result["pending"][0]["record_type"] == expected
    record = store.structured.get(result["pending"][0]["record_id"])
    assert record["status"] == "pending"
    assert record["provenance"] == "observed"
    assert record["origin"]["capture"] == "auto"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Mujhe shayad Roman Urdu pasand hai.", "uncertain"),
        ("Mujhe abhi Roman Urdu pasand hai.", "temporary_context"),
        ("Main hamesha mosque visit karta hoon.", "sensitive_attribute"),
        ("Main hamesha dawa leta hoon.", "sensitive_attribute"),
        ("میں ہمیشہ دوا لیتا ہوں۔", "sensitive_attribute"),
        ("میں ہمیشہ نماز پڑھتا ہوں۔", "sensitive_attribute"),
    ],
)
def test_urdu_memory_capture_keeps_uncertain_temporary_and_sensitive_content_out(
    tmp_path, text, reason
):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, text)

    assert result["saved"] == []
    assert result["pending"] == []
    assert reason in [item["reason"] for item in result["skipped"]]
    assert records(store) == []


def test_urdu_script_tokens_support_duplicate_comparison():
    assert similarity("مجھے اردو میں بات پسند ہے", "مجھے اردو میں بات پسند ہے") == 1.0

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Yaad rakhna ke hum ne Mia ke liye SQLite ka faisla kiya.", "decision"),
        ("یاد رکھنا کہ میرا مقصد Mia کو مکمل کرنا ہے۔", "goal"),
    ],
)
def test_explicit_memory_commands_support_roman_urdu_and_urdu(tmp_path, text, expected):
    store, service, conv = make(tmp_path)
    result = say(store, service, conv, text)

    assert result["explicit"] is True
    assert len(result["saved"]) == 1
    record = store.structured.get(result["saved"][0]["record_id"])
    assert record["record_type"] == expected
    assert record["provenance"] == "owner_provided"
    assert record["origin"]["capture"] == "explicit"


def test_roman_urdu_forget_command_removes_matching_memory(tmp_path):
    store, service, conv = make(tmp_path)
    saved = say(store, service, conv, "Remember that my favorite editor is Neovim.")
    record_id = saved["saved"][0]["record_id"]

    forgotten = say(store, service, conv, "Bhool jao favorite editor Neovim.")

    assert forgotten["explicit"] is True
    assert record_id in [item["record_id"] for item in forgotten["forgotten"]]
    assert store.structured.get(record_id) is None



# ------------------------------------------------------- model-assisted extraction


def test_model_extraction_is_pending_even_when_auto_capture_is_enabled(tmp_path):
    store, _, conv = make(tmp_path)
    calls = []

    def extractor(sentence):
        calls.append(sentence)
        return {
            "record_type": "episode",
            "title": "Release incident",
            "body": sentence,
            "confidence": 0.84,
        }

    service = CaptureService(store, model_extractor=extractor)
    store.settings.update({"capture_mode": "auto"})
    result = say(
        store, service, conv,
        "Last Tuesday the release rollback took forty minutes because the cache was stale.",
    )
    assert len(calls) == 1
    assert result["saved"] == [] and len(result["pending"]) == 1
    record = store.structured.get(result["pending"][0]["record_id"])
    assert record["status"] == "pending"
    assert record["provenance"] == "observed"
    assert record["origin"]["capture"] == "model_assisted"
    assert record["evidence"][0]["type"] == "message"


def test_model_extraction_rejects_paraphrased_or_invented_source_text(tmp_path):
    store, _, conv = make(tmp_path)

    def extractor(sentence):
        return {
            "record_type": "fact",
            "title": "Invented detail",
            "body": "The owner works remotely every day.",
            "confidence": 0.8,
        }

    service = CaptureService(store, model_extractor=extractor)
    result = say(
        store, service, conv,
        "Last Tuesday the release rollback took forty minutes because the cache was stale.",
    )
    assert result["pending"] == [] and result["saved"] == []
    assert records(store) == []


def test_model_extraction_never_runs_for_sensitive_or_uncertain_sentences(tmp_path):
    store, _, conv = make(tmp_path)
    calls = []

    def extractor(sentence):
        calls.append(sentence)
        return None

    service = CaptureService(store, model_extractor=extractor)
    result = say(store, service, conv, "Maybe I prefer the political party my family votes for.")
    assert calls == []
    assert result["pending"] == [] and records(store) == []


def test_model_extractor_failure_does_not_break_chat_or_capture(tmp_path):
    store, _, conv = make(tmp_path)

    def extractor(_sentence):
        raise RuntimeError("provider unavailable")

    service = CaptureService(store, model_extractor=extractor)
    result = say(
        store, service, conv,
        "Last Tuesday the release rollback took forty minutes because the cache was stale.",
    )
    assert result["processed"] is True
    assert result["pending"] == [] and result["saved"] == []
