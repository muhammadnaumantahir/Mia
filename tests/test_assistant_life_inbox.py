from pathlib import Path

from fastapi.testclient import TestClient

from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.life.store import LifeStore
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"


def build_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_LIFE_ENABLED", "false")
    life = LifeStore(tmp_path / "life.sqlite3")
    app = create_app(
        store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
        gateway=ModelGateway(ProviderRegistry()),
        memory=MemoryStore(tmp_path / "memory.sqlite3"),
        life=life,
    )
    return TestClient(app), life


def test_assistant_page_exposes_owner_inbox_and_safe_actions():
    source = PAGE.read_text(encoding="utf-8")
    assert 'id="lifeInboxToggle"' in source
    assert 'id="lifeInboxPanel"' in source
    assert 'id="lifeInboxMessages"' in source
    assert "/api/life/outreach?limit=30" in source
    assert "/api/life/outreach/'+encodeURIComponent(messageId)+'/state" in source
    assert "Mark read" in source and "Dismiss topic" in source and "Done" in source
    assert "window.setInterval(loadLifeInbox,60000)" in source
    # Dynamic outreach data must never be parsed as HTML or executable code.
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in source


def test_assistant_inbox_reads_real_outreach_and_supports_owner_actions(tmp_path, monkeypatch):
    client, life = build_client(tmp_path, monkeypatch)
    try:
        message_id = life.add_outreach(
            kind="research_complete",
            topic_key="mia:research:test",
            title="Research note ready",
            body="Mia found two relevant sources for your saved question.",
            evidence=[{"type": "cycle", "ref": "cycle-123"}],
        )

        page = client.get("/assistant")
        assert page.status_code == 200
        assert "Mia Inbox" in page.text

        inbox = client.get("/api/life/outreach?limit=30")
        assert inbox.status_code == 200
        messages = inbox.json()["messages"]
        assert len(messages) == 1
        assert messages[0]["message_id"] == message_id
        assert messages[0]["state"] == "pending"

        read = client.post(
            f"/api/life/outreach/{message_id}/state",
            json={"state": "read"},
        )
        assert read.status_code == 200
        assert life.get_outreach(message_id)["state"] == "read"

        done = client.post(
            f"/api/life/outreach/{message_id}/state",
            json={"state": "acted"},
        )
        assert done.status_code == 200
        assert life.get_outreach(message_id)["state"] == "acted"
    finally:
        client.close()


def test_assistant_inbox_rejects_invalid_state(tmp_path, monkeypatch):
    client, life = build_client(tmp_path, monkeypatch)
    try:
        message_id = life.add_outreach(
            kind="goal_followup",
            topic_key="goal:one",
            title="Goal check-in",
            body="A saved goal has not been updated recently.",
            evidence=[{"type": "record", "ref": "record-1"}],
        )
        response = client.post(
            f"/api/life/outreach/{message_id}/state",
            json={"state": "sent"},
        )
        assert response.status_code == 400
        assert life.get_outreach(message_id)["state"] == "pending"
    finally:
        client.close()
