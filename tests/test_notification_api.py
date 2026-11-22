"""MIA-LIFE-09: owner HTTP routes for the notification center."""

import pytest
from fastapi.testclient import TestClient

from mia.life.notifications import NotificationCenter
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


@pytest.fixture()
def env(tmp_path):
    center = NotificationCenter(tmp_path / "n.sqlite3")
    app = create_app(store=ObservatoryStore(tmp_path / "o.sqlite3"), notifications=center)
    return TestClient(app), center


def seed(center, key="a", **kw):
    args = dict(source_type="demo", source_ref=f"demo-{key}", title=f"T {key}", body="b",
                reason="It is due", relevance=0.9)
    args.update(kw)
    return center.submit(**args)["notification_id"]


def test_list_returns_visible_items_counts_and_settings(env):
    client, center = env
    seed(center)
    body = client.get("/api/notifications").json()
    assert len(body["notifications"]) == 1 and body["counts"] == {"pending": 1}
    assert body["settings"]["timezone"] == "UTC"
    assert client.get("/api/notifications?state=bogus").status_code == 400
    assert client.get("/api/notifications?state=dismissed").json()["notifications"] == []


def test_read_snooze_dismiss_and_explain_flow(env):
    client, center = env
    n = seed(center)
    assert client.post(f"/api/notifications/{n}/read").json()["notification"]["state"] == "read"
    snoozed = client.post(f"/api/notifications/{n}/snooze", json={"minutes": 30}).json()["notification"]
    assert snoozed["state"] == "snoozed"
    why = client.get(f"/api/notifications/{n}/explain").json()
    assert why["why"] == "It is due" and why["source"] == {"type": "demo", "ref": "demo-a"}
    assert [h["event"] for h in why["history"]][-2:] == ["read", "snoozed"]
    assert client.post(f"/api/notifications/{n}/dismiss").json()["notification"]["state"] == "dismissed"


def test_errors_are_clean_400_and_404(env):
    client, center = env
    n = seed(center)
    assert client.post("/api/notifications/ntf-nope/dismiss").status_code == 404
    assert client.get("/api/notifications/ntf-nope/explain").status_code == 404
    assert client.post(f"/api/notifications/{n}/snooze", json={"minutes": 0}).status_code == 400
    assert client.post(f"/api/notifications/{n}/snooze", json={"extra": 1}).status_code == 422
    client.post(f"/api/notifications/{n}/dismiss")
    assert client.post(f"/api/notifications/{n}/read").status_code == 400


def test_settings_round_trip_and_validation(env):
    client, _ = env
    ok = client.put("/api/notifications/settings", json={"timezone": "Asia/Karachi", "quiet_hours_enabled": True})
    assert ok.status_code == 200 and ok.json()["settings"]["timezone"] == "Asia/Karachi"
    assert client.get("/api/notifications/settings").json()["settings"]["quiet_hours_enabled"] is True
    assert client.put("/api/notifications/settings", json={}).status_code == 400
    assert client.put("/api/notifications/settings", json={"timezone": "Nowhere/Land"}).status_code == 400
    assert client.put("/api/notifications/settings", json={"surprise": 1}).status_code == 422


def test_there_is_no_http_route_to_create_notifications(env):
    client, _ = env
    for method in ("post", "put"):
        response = getattr(client, method)("/api/notifications", json={"title": "x", "reason": "y"})
        assert response.status_code in {404, 405}


def test_voice_digest_route_is_opt_in_and_announces_each_item_once(env):
    client, center = env
    seed(center)
    assert client.post("/api/notifications/voice-digest").json() == {"items": []}  # voice channel off
    center.update_settings({"voice_enabled": True})
    seed(center, "b")
    first = client.post("/api/notifications/voice-digest").json()["items"]
    assert [i["title"] for i in first] == ["T b"]
    assert client.post("/api/notifications/voice-digest").json() == {"items": []}
