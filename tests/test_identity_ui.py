from fastapi.testclient import TestClient

from mia.observatory.api import create_app


def test_identity_page_serves_owner_profile_editor(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(create_app())

    response = client.get("/identity")

    assert response.status_code == 200
    assert "Mia — Identity" in response.text
    assert 'id="save"' in response.text
    assert 'id="preview"' in response.text
    assert 'id="history"' in response.text
    assert 'request("/api/identity")' in response.text
    assert 'request("/api/identity/history?limit=50")' in response.text


def test_identity_editor_apis_update_preview_history_and_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    client = TestClient(create_app())

    initial = client.get("/api/identity")
    assert initial.status_code == 200
    initial_version = initial.json()["version"]

    updated = client.put(
        "/api/identity",
        json={
            "name": "Mia",
            "purpose": "A thoughtful personal AI assistant",
            "owner_relationship": "Personal assistant",
            "personality": ["curious", "helpful"],
            "principles": ["be honest"],
            "interaction_preferences": ["speak naturally"],
            "communication_style": {
                "tone": "warm",
                "verbosity": "balanced",
                "language": "English and Roman Urdu",
            },
            "reason": "Identity editor integration test",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["changed"] is True
    assert updated.json()["profile"]["name"] == "Mia"
    assert "Mia" in updated.json()["prompt_preview"]

    history = client.get("/api/identity/history?limit=50")
    assert history.status_code == 200
    assert any(v["version"] == initial_version for v in history.json()["versions"])
    assert any(v["version"] == updated.json()["version"] for v in history.json()["versions"])

    restored = client.post(
        "/api/identity/rollback",
        json={"version": initial_version, "reason": "Verify editor rollback"},
    )
    assert restored.status_code == 200
    assert restored.json()["changed"] is True
    assert restored.json()["version"] > updated.json()["version"]
