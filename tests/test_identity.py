import json
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from mia.gateway.contracts import ModelCandidate, ModelRequest, StreamChunk, TokenUsage
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.identity import (
    DEFAULT_PROFILE,
    IDENTITY_PROMPT,
    TOOL_AND_DATA_POLICY,
    identity_system_prompt,
    render_identity_prompt,
)
from mia.memory.schema import LATEST_VERSION
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

# ----------------------------------------------------------------- store level


def test_factory_identity_is_seeded_and_matches_legacy_prompt(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    active = memory.identity.active()
    assert active["version"] == 1 and active["source"] == "system"
    assert active["profile"] == DEFAULT_PROFILE
    assert memory.identity.system_prompt() == f"{IDENTITY_PROMPT} {TOOL_AND_DATA_POLICY}"
    assert memory.identity.system_prompt() == identity_system_prompt()


def test_factory_profile_hardcodes_no_owner_details():
    profile = DEFAULT_PROFILE
    assert profile["owner_relationship"] == ""
    assert profile["interaction_preferences"] == []
    assert profile["purpose"] == ""
    assert "owner" not in profile["name"].lower()


def test_update_creates_immutable_versions_and_noop_creates_none(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    first = memory.identity.update(
        {"personality": ["warm", "curious"], "communication_style": {"verbosity": "concise"}},
        reason="initial setup",
    )
    assert first["changed"] is True and first["version"] == 2
    assert first["parent_version"] == 1 and first["source"] == "owner"

    again = memory.identity.update(
        {"personality": ["warm", "curious"], "communication_style": {"verbosity": "concise"}}
    )
    assert again["changed"] is False and again["version"] == 2

    memory.identity.update({"communication_style": {"tone": "plain"}})
    active = memory.identity.active()
    assert active["version"] == 3
    # nested style keys merge instead of replacing the whole object
    assert active["profile"]["communication_style"] == {
        "tone": "plain",
        "verbosity": "concise",
        "language": "",
    }
    # version 1 is untouched
    assert memory.identity.get(1)["profile"] == DEFAULT_PROFILE
    assert [v["version"] for v in memory.identity.history()] == [3, 2, 1]


def test_identity_and_history_survive_restart(tmp_path):
    path = tmp_path / "m.sqlite3"
    memory = MemoryStore(path)
    memory.identity.update({"purpose": "Help the owner plan projects"}, reason="setup")
    memory.identity.update({"name": "Mia", "interaction_preferences": ["ask before assuming"]})
    expected = memory.identity.system_prompt()

    reopened = MemoryStore(path)
    assert reopened.identity.active()["version"] == 3
    assert reopened.identity.system_prompt() == expected
    assert "Help the owner plan projects" in expected
    assert len(reopened.identity.history()) == 3  # reopening did not add a version


def test_rollback_restores_old_profile_as_new_version(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    memory.identity.update({"purpose": "first purpose"})
    memory.identity.update({"purpose": "second purpose"})
    restored = memory.identity.rollback(2, reason="changed my mind")
    assert restored["changed"] is True and restored["version"] == 4
    assert restored["profile"]["purpose"] == "first purpose"
    assert restored["parent_version"] == 3
    assert memory.identity.get(3)["profile"]["purpose"] == "second purpose"  # history intact
    noop = memory.identity.rollback(2)
    assert noop["changed"] is False and noop["version"] == 4
    with pytest.raises(KeyError):
        memory.identity.rollback(99)


@pytest.mark.parametrize(
    "changes",
    [
        {"purpose": "my password is hunter2hunter2"},
        {"principles": ["use token ghp_abcdefghijklmnopqrstuvwxyz0123456789"]},
        {"owner_relationship": "api_key=sk-abcdefghijklmnopqrstuvwxyz"},
        {"unknown_field": "x"},
        {"communication_style": {"verbosity": "chatty"}},
        {"communication_style": {"colour": "blue"}},
        {"name": "x" * 41},
        {"personality": "not a list"},
        {"personality": ["a"] * 11},
        {"purpose": 123},
    ],
)
def test_invalid_identity_changes_are_rejected_and_change_nothing(tmp_path, changes):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    with pytest.raises(ValueError):
        memory.identity.update(changes)
    assert memory.identity.active()["version"] == 1
    assert len(memory.identity.history()) == 1


def test_secret_rejection_does_not_echo_the_secret(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    with pytest.raises(ValueError) as caught:
        memory.identity.update({"purpose": "password: SuperSecretValue99"})
    assert "SuperSecretValue99" not in str(caught.value)


def test_only_owner_system_or_import_may_change_identity(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    for source in ("model", "assistant", "ai", ""):
        with pytest.raises(ValueError):
            memory.identity.update({"purpose": "hijack"}, source=source)
    assert memory.identity.active()["version"] == 1


def test_identity_changes_are_audited_without_copying_values(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    memory.identity.update({"purpose": "a very private purpose"}, reason="setup")
    memory.identity.rollback(1, reason="undo")
    entries = memory.audit.list(target_type="identity")
    actions = [e["action"] for e in entries]
    assert actions == ["identity_rolled_back", "identity_updated", "identity_created"]
    updated = entries[1]
    assert updated["actor"] == "owner"
    assert updated["detail"]["changed_fields"] == ["purpose"]
    assert "private purpose" not in json.dumps(entries)


def test_name_and_profile_render_into_prompt_but_core_rules_remain():
    profile = {
        **DEFAULT_PROFILE,
        "name": "Nova",
        "purpose": "Keep projects on track",
        "personality": ["calm"],
        "communication_style": {"tone": "friendly", "verbosity": "concise", "language": "English"},
        "interaction_preferences": ["give the answer first"],
    }
    prompt = render_identity_prompt(profile)
    assert prompt.startswith("You are Nova,")
    assert "Keep projects on track" in prompt and "give the answer first" in prompt
    assert "Be honest that you are an AI" in prompt  # fixed core cannot be edited away
    assert prompt.endswith(TOOL_AND_DATA_POLICY)


def test_concurrent_updates_are_serialised_without_losing_either(tmp_path):
    memory = MemoryStore(tmp_path / "m.sqlite3")
    errors: list[Exception] = []

    def change(changes):
        try:
            memory.identity.update(changes)
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=change, args=({"purpose": "purpose from thread"},)),
        threading.Thread(target=change, args=({"owner_relationship": "relationship text"},)),
        threading.Thread(target=change, args=({"personality": ["steady"]},)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    active = memory.identity.active()
    assert active["version"] == 4
    assert active["profile"]["purpose"] == "purpose from thread"
    assert active["profile"]["owner_relationship"] == "relationship text"
    assert active["profile"]["personality"] == ["steady"]


# ------------------------------------------------------------------ migrations


def _drop_new_tables(path):
    with sqlite3.connect(path) as db:
        for table in ("identity_versions", "memory_audit_log", "schema_migrations"):
            db.execute(f"DROP TABLE {table}")


def test_migration_preserves_existing_memories(tmp_path):
    path = tmp_path / "m.sqlite3"
    memory = MemoryStore(path)
    conversation = memory.ensure_conversation()
    memory.append_message(conversation, "user", "We chose Postgres for billing.")
    item_id = memory.add_item(
        kind="fact", title="Billing DB", content="Postgres for billing", trust_level="user"
    )
    record_id, _ = memory.structured.add(
        record_type="decision", title="Use Postgres", body="Chosen for transactions"
    )
    _drop_new_tables(path)  # recreate the pre-identity (schema 1) layout

    upgraded = MemoryStore(path)
    assert upgraded.get_item(item_id)["title"] == "Billing DB"
    assert upgraded.get_messages(conversation, limit=10)[0]["content"].startswith("We chose")
    assert upgraded.structured.get(record_id)["title"] == "Use Postgres"
    assert upgraded.identity.active()["version"] == 1
    with sqlite3.connect(path) as db:
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY 1")]
    assert versions == list(range(1, LATEST_VERSION + 1))

    again = MemoryStore(path)  # idempotent
    assert len(again.identity.history()) == 1


def test_database_from_a_newer_mia_is_refused_not_damaged(tmp_path):
    path = tmp_path / "m.sqlite3"
    MemoryStore(path)
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO schema_migrations (version, description, applied_at) "
            "VALUES (999, 'future', 'x')"
        )
    with pytest.raises(RuntimeError, match="newer"):
        MemoryStore(path)


# ------------------------------------------------------------------- API level


class RecordingProvider:
    def __init__(self, provider_id, model_id):
        self.provider_id = provider_id
        self.model_id = model_id
        self.requests: list[ModelRequest] = []

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id=self.model_id,
                capabilities=frozenset({"chat"}),
                context_window=8192,
                free_tier=True,
                healthy=True,
                enabled=True,
                data_local=True,
            )
        ]

    async def complete(self, candidate, request: ModelRequest):
        self.requests.append(request)
        return "ok"

    async def stream(self, candidate, request: ModelRequest):
        self.requests.append(request)
        yield StreamChunk(delta="ok")
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(3, 1))

    @property
    def system_prompt(self):
        return self.requests[-1].messages[0]["content"]


def make_client(tmp_path, monkeypatch, provider, *, memory=None):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    registry = ProviderRegistry()
    registry.register(provider)
    memory = memory or MemoryStore(tmp_path / "memory.sqlite3")
    observatory = ObservatoryStore(tmp_path / "observatory.sqlite3")
    client = TestClient(
        create_app(store=observatory, gateway=ModelGateway(registry), memory=memory)
    )
    return client, memory, observatory


def test_identity_api_update_history_rollback_and_audit(tmp_path, monkeypatch):
    client, _, _ = make_client(tmp_path, monkeypatch, RecordingProvider("p", "m"))
    initial = client.get("/api/identity").json()
    assert initial["version"] == 1 and "You are Mia" in initial["prompt_preview"]

    updated = client.put(
        "/api/identity",
        json={
            "purpose": "Help me finish my thesis",
            "communication_style": {"verbosity": "concise"},
            "reason": "first setup",
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["version"] == 2 and body["changed"] is True
    assert "Help me finish my thesis" in body["prompt_preview"]

    unchanged = client.put("/api/identity", json={"purpose": "Help me finish my thesis"}).json()
    assert unchanged["changed"] is False and unchanged["version"] == 2

    history = client.get("/api/identity/history").json()["versions"]
    assert [v["version"] for v in history] == [2, 1]

    rolled = client.post("/api/identity/rollback", json={"version": 1, "reason": "undo"})
    assert rolled.status_code == 200 and rolled.json()["version"] == 3
    assert rolled.json()["profile"]["purpose"] == ""
    assert client.post("/api/identity/rollback", json={"version": 50}).status_code == 404

    audit = client.get("/api/memory/audit", params={"target_type": "identity"}).json()["entries"]
    assert [e["action"] for e in audit][:2] == ["identity_rolled_back", "identity_updated"]


def test_identity_api_rejects_secrets_unknown_fields_and_bad_values(tmp_path, monkeypatch):
    client, memory, _ = make_client(tmp_path, monkeypatch, RecordingProvider("p", "m"))
    secret = client.put("/api/identity", json={"purpose": "token: ghp_" + "a" * 36})
    assert secret.status_code == 400 and "ghp_" not in secret.text
    assert client.put("/api/identity", json={"model": "gpt"}).status_code == 422
    assert client.put("/api/identity", json={"communication_style": {"verbosity": "x"}}).status_code == 400
    assert client.put("/api/identity", json={"personality": ["a"] * 60}).status_code == 422
    assert memory.identity.active()["version"] == 1


def test_identity_is_identical_across_restart_and_provider_switch(tmp_path, monkeypatch):
    provider_a = RecordingProvider("local-a", "model-alpha")
    client_a, memory_a, _ = make_client(tmp_path, monkeypatch, provider_a)
    client_a.put(
        "/api/identity",
        json={"purpose": "Support my research", "personality": ["steady"], "reason": "setup"},
    )
    reply = client_a.post("/api/gateway/chat", json={"prompt": "hello there", "use_tools": False})
    assert reply.status_code == 200
    prompt_a = provider_a.system_prompt
    assert "Support my research" in prompt_a

    # "Restart" with a different provider and model: fresh app, store and provider objects.
    provider_b = RecordingProvider("local-b", "model-beta")
    client_b, memory_b, _ = make_client(
        tmp_path, monkeypatch, provider_b, memory=MemoryStore(tmp_path / "memory.sqlite3")
    )
    assert memory_b is not memory_a
    reply = client_b.post("/api/gateway/chat", json={"prompt": "hello again", "use_tools": False})
    assert reply.status_code == 200
    assert provider_b.system_prompt == prompt_a
    for model_label in ("model-alpha", "model-beta", "local-a", "local-b"):
        assert model_label not in prompt_a


def test_streaming_chat_also_uses_the_stored_identity(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a", "model-alpha")
    client, _, _ = make_client(tmp_path, monkeypatch, provider)
    client.put("/api/identity", json={"purpose": "Keep me organised"})
    response = client.post(
        "/api/gateway/chat/stream", json={"prompt": "hello there", "use_tools": False}
    )
    assert response.status_code == 200
    assert "Keep me organised" in provider.system_prompt


def test_identity_change_applies_to_the_next_reply_without_restart(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a", "model-alpha")
    client, _, _ = make_client(tmp_path, monkeypatch, provider)
    client.post("/api/gateway/chat", json={"prompt": "first message", "use_tools": False})
    assert "Be brief" not in provider.system_prompt
    client.put("/api/identity", json={"interaction_preferences": ["Be brief"]})
    client.post("/api/gateway/chat", json={"prompt": "second message", "use_tools": False})
    assert "Be brief" in provider.system_prompt


def test_identity_storage_failure_does_not_break_chat(tmp_path, monkeypatch):
    provider = RecordingProvider("local-a", "model-alpha")
    client, memory, observatory = make_client(tmp_path, monkeypatch, provider)

    def broken():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(memory.identity, "active", broken)
    response = client.post("/api/gateway/chat", json={"prompt": "hello", "use_tools": False})
    assert response.status_code == 200
    assert provider.system_prompt == identity_system_prompt()
    events = observatory.list_events(limit=50)
    assert any(e["event_type"] == "identity_load_failure" for e in events)



@pytest.mark.parametrize("endpoint", ["/api/gateway/chat", "/api/gateway/chat/stream"])
def test_text_and_streaming_chat_receive_canonical_sps_self_description(
    tmp_path, monkeypatch, endpoint
):
    provider = RecordingProvider("local-a", "model-alpha")
    client, _, _ = make_client(tmp_path, monkeypatch, provider)

    response = client.post(endpoint, json={"prompt": "Tell me about your architecture", "use_tools": False})

    assert response.status_code == 200
    prompt = provider.system_prompt
    assert "CANONICAL SPS ARCHITECTURE" in prompt
    assert "SPS-01 Software DNA Core" in prompt
    assert "SPS-10 Execution Core" in prompt
    assert "Configured provider adapters (1): local-a" in prompt
    assert "does not prove every feature is implemented or enabled" in prompt
