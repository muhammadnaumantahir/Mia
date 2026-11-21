"""Cross-session continuity when the serving provider changes (MIA-BRAIN-02).

These use deterministic scripted providers. They verify that Mia's persistent memory,
not whichever model answered last, owns continuity, and that privacy routing still
holds after a switch. They say nothing about live model answer quality.
"""

from .test_brain_pipeline import Scripted, chat, client_for

BILLING_Q = "Which database did we choose for the billing service?"


def _seen_text(provider) -> str:
    return " ".join(str(m.get("content")) for req in provider.seen for m in req.messages)


def _decision(memory, title="Billing database", body="Use PostgreSQL for the billing service."):
    record_id, _ = memory.structured.add(record_type="decision", title=title, body=body)
    return record_id


def test_memory_recalled_in_new_session_after_provider_switch(tmp_path, monkeypatch):
    first = Scripted("first", reply="Noted.")
    second = Scripted("second", reply="PostgreSQL.", enabled=False)
    client, memory, _ = client_for(tmp_path, monkeypatch, first, second)
    _decision(memory)

    assert chat(client, "Let's talk about invoices today.",
                conversation_id="session-one-aaa").json()["provider_id"] == "first"

    first.enabled, second.enabled = False, True
    reply = chat(client, BILLING_Q, conversation_id="session-two-bbb").json()

    assert reply["provider_id"] == "second"
    assert "PostgreSQL" in _seen_text(second)  # recalled from memory, not from the old provider


def test_private_memory_is_not_sent_to_a_remote_provider_after_switch(tmp_path, monkeypatch):
    local = Scripted("local", reply="ok")
    remote = Scripted("remote", reply="remote answer", local=False, enabled=False)
    client, memory, _ = client_for(tmp_path, monkeypatch, local, remote)
    monkeypatch.delenv("MIA_ALLOW_REMOTE_FILE_CONTEXT", raising=False)
    _decision(memory)

    # Positive control: the local model does receive the memory, so the absence
    # asserted below is the privacy rule at work and not a retrieval miss.
    chat(client, BILLING_Q, conversation_id="session-local-ccc")
    assert "PostgreSQL" in _seen_text(local)

    local.enabled, remote.enabled = False, True  # only a remote model is left
    chat(client, BILLING_Q, conversation_id="session-remote-ccc")

    assert "PostgreSQL" not in _seen_text(remote)


def test_owner_correction_wins_after_provider_switch(tmp_path, monkeypatch):
    first = Scripted("first", reply="ok")
    second = Scripted("second", reply="MySQL.", enabled=False)
    client, memory, _ = client_for(tmp_path, monkeypatch, first, second)
    old_id = _decision(memory)

    chat(client, BILLING_Q, conversation_id="session-before-ddd")
    memory.structured.supersede(old_id, body="Use MySQL for the billing service.")

    first.enabled, second.enabled = False, True
    chat(client, BILLING_Q, conversation_id="session-after-eee")

    seen = _seen_text(second)
    assert "MySQL" in seen
    assert "PostgreSQL" not in seen
