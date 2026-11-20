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
