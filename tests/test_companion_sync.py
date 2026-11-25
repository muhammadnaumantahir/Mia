"""MIA-LIFE-13: authenticated, versioned and revocable companion sync."""

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.security.pairing import PairingService, auth_statement, b64e, pairing_statement
from mia.security.sync import CompanionSyncError, CompanionSyncStore


class Device:
    def __init__(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = b64e(
            self.key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        )
        self.device_id = ""

    def sign(self, message: bytes) -> str:
        return b64e(self.key.sign(message))

    def pair(self, service: PairingService, scopes: list[str]) -> str:
        code = service.create_code("Sync test device", scopes)["code"]
        body = {
            "code": code,
            "public_key": self.public,
            "device_name": "Sync test device",
            "signature": self.sign(pairing_statement(code, self.public, "Sync test device")),
        }
        self.device_id = service.redeem(**body)["device_id"]
        return self.login(service)

    def login(self, service: PairingService) -> str:
        challenge = service.challenge(self.device_id)
        signature = self.sign(auth_statement(
            self.device_id, challenge["challenge_id"], challenge["nonce"]
        ))
        return service.open_session(
            self.device_id, challenge["challenge_id"], signature
        )["token"]


@pytest.fixture
def sync_api(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    app = create_app(store=ObservatoryStore(tmp_path / "observatory.sqlite3"))
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    return app, client, app.state.pairing


def headers(token):
    return {"Authorization": "Bearer " + token}


def test_scoped_sync_push_pull_conflict_tombstone_and_revocation(sync_api):
    app, client, service = sync_api
    writer = Device()
    writer_token = writer.pair(service, ["companion.sync.read", "companion.sync.write"])
    reader = Device()
    reader_token = reader.pair(service, ["companion.sync.read"])
    write_only = Device()
    write_only_token = write_only.pair(service, ["companion.sync.write"])

    payload = {"theme": "dark", "sidebar_collapsed": True}
    first_batch = {
        "batch_id": "batch-000000000001",
        "changes": [{
            "namespace": "preferences", "item_id": "ui", "base_version": 0,
            "payload": payload,
        }],
    }
    pushed = client.post(
        "/api/companion/sync/push", json=first_batch, headers=headers(writer_token)
    )
    assert pushed.status_code == 200, pushed.text
    assert pushed.json()["items"][0]["version"] == 1

    replay = client.post(
        "/api/companion/sync/push", json=first_batch, headers=headers(writer_token)
    )
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["items"][0]["version"] == 1

    pulled = client.get(
        "/api/companion/sync/pull?since=0", headers=headers(reader_token)
    )
    assert pulled.status_code == 200
    assert pulled.json()["changes"][0]["payload"] == payload
    assert pulled.json()["next_cursor"] == 1

    wrong_scope_push = client.post(
        "/api/companion/sync/push", json=first_batch, headers=headers(reader_token)
    )
    assert wrong_scope_push.status_code == 403
    wrong_scope_pull = client.get(
        "/api/companion/sync/pull", headers=headers(write_only_token)
    )
    assert wrong_scope_pull.status_code == 403

    conflict = client.post(
        "/api/companion/sync/push",
        headers=headers(write_only_token),
        json={
            "batch_id": "batch-000000000002",
            "changes": [{
                "namespace": "preferences", "item_id": "ui", "base_version": 0,
                "payload": {"theme": "light"},
            }],
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["conflicts"][0]["current_version"] == 1

    updated = client.post(
        "/api/companion/sync/push",
        headers=headers(writer_token),
        json={
            "batch_id": "batch-000000000003",
            "changes": [{
                "namespace": "preferences", "item_id": "ui", "base_version": 1,
                "payload": {"theme": "light"},
            }],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["items"][0]["version"] == 2

    deleted = client.post(
        "/api/companion/sync/push",
        headers=headers(writer_token),
        json={
            "batch_id": "batch-000000000004",
            "changes": [{
                "namespace": "preferences", "item_id": "ui", "base_version": 2,
                "deleted": True,
            }],
        },
    )
    assert deleted.status_code == 200
    assert deleted.json()["items"][0]["version"] == 3
    changes = client.get(
        "/api/companion/sync/pull?since=1", headers=headers(reader_token)
    ).json()["changes"]
    assert [item["version"] for item in changes] == [2, 3]
    assert changes[-1]["deleted"] is True and changes[-1]["payload"] is None

    assert service.revoke(writer.device_id) is True
    revoked = client.get(
        "/api/companion/sync/pull?since=0", headers=headers(writer_token)
    )
    assert revoked.status_code == 401


def test_remote_https_sync_and_signed_session_renewal(tmp_path, monkeypatch):
    monkeypatch.setenv("MIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MIA_REMOTE_ACCESS_ENABLED", "true")
    monkeypatch.setenv("MIA_REMOTE_ALLOWED_HOSTS", "mia.example.test")
    app = create_app(
        store=ObservatoryStore(tmp_path / "remote-observatory.sqlite3"),
        local_guard=True,
    )
    client = TestClient(app, base_url="https://mia.example.test")
    device = Device()
    token = device.pair(
        app.state.pairing, ["companion.sync.read", "companion.sync.write"]
    )

    pull = client.get("/api/companion/sync/pull", headers=headers(token))
    assert pull.status_code == 200

    challenge = client.post(
        "/api/companion/challenge",
        json={"device_id": device.device_id},
        headers=headers(token),
    )
    assert challenge.status_code == 200
    renewed = client.post(
        "/api/companion/session",
        json={
            "device_id": device.device_id,
            "challenge_id": challenge.json()["challenge_id"],
            "signature": device.sign(auth_statement(
                device.device_id, challenge.json()["challenge_id"], challenge.json()["nonce"]
            )),
        },
        headers=headers(token),
    )
    assert renewed.status_code == 200
    new_token = renewed.json()["token"]
    assert new_token.startswith("mia-dev_")

    pushed = client.post(
        "/api/companion/sync/push",
        headers=headers(new_token),
        json={
            "batch_id": "remote-batch-000001",
            "changes": [{
                "namespace": "companion_state",
                "item_id": "last_seen_screen",
                "base_version": 0,
                "payload": {"screen": "home"},
            }],
        },
    )
    assert pushed.status_code == 200

    # Device credentials still cannot reach owner administration, even over HTTPS.
    assert client.get("/api/companion-admin/devices", headers=headers(new_token)).status_code == 403
    assert client.post("/api/companion/pair", json={}, headers=headers(new_token)).status_code == 400


def test_sync_push_is_atomic_and_replay_is_idempotent(tmp_path):
    store = CompanionSyncStore(tmp_path / "sync.sqlite3")
    first = store.push("device-one", "batch-000000000001", [{
        "namespace": "preferences", "item_id": "theme", "base_version": 0,
        "payload": {"value": "dark"}, "deleted": False,
    }])
    assert first["status"] == "applied"

    conflict = store.push("device-two", "batch-000000000002", [
        {
            "namespace": "preferences", "item_id": "new-item", "base_version": 0,
            "payload": {"value": "must-not-apply"}, "deleted": False,
        },
        {
            "namespace": "preferences", "item_id": "theme", "base_version": 0,
            "payload": {"value": "stale"}, "deleted": False,
        },
    ])
    assert conflict["status"] == "conflict"
    after_conflict = store.pull("device-two")["changes"]
    assert len(after_conflict) == 1
    assert after_conflict[0]["item_id"] == "theme"
    assert after_conflict[0]["version"] == 1
    assert after_conflict[0]["payload"] == {"value": "dark"}

    replay = store.push("device-one", "batch-000000000001", [{
        "namespace": "preferences", "item_id": "theme", "base_version": 0,
        "payload": {"value": "different"}, "deleted": False,
    }])
    assert replay["replayed"] is True and replay["items"][0]["version"] == 1
    assert len(store.pull("device-two")["changes"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"namespace": "memory", "item_id": "x", "base_version": 0, "payload": {}},
        {"namespace": "preferences", "item_id": "x", "base_version": -1, "payload": {}},
        {"namespace": "preferences", "item_id": "x", "base_version": 0, "payload": {"api_key": "secret"}},
        {"namespace": "preferences", "item_id": "x", "base_version": 0, "payload": {"privateKey": "not-stored"}},
        {"namespace": "preferences", "item_id": "x", "base_version": 0, "payload": {"note": "Bearer abcdefghijklmnopqrstuvwxyz0123456789"}},
        {"namespace": "preferences", "item_id": "../escape", "base_version": 0, "payload": {}},
        {"namespace": "preferences", "item_id": "x", "base_version": 0, "deleted": True, "payload": {}},
    ],
)
def test_sync_store_rejects_unsafe_or_invalid_changes(tmp_path, change):
    store = CompanionSyncStore(tmp_path / "sync.sqlite3")
    with pytest.raises(CompanionSyncError):
        store.push("device-one", "batch-000000000001", [change])


def test_sync_store_enforces_batch_and_payload_size_bounds(tmp_path):
    store = CompanionSyncStore(tmp_path / "sync.sqlite3")
    with pytest.raises(CompanionSyncError, match="1 to 50"):
        store.push(
            "device-one", "batch-000000000099",
            [{"namespace": "preferences", "item_id": f"i-{n}", "base_version": 0,
              "payload": {"value": n}} for n in range(51)],
        )
    with pytest.raises(CompanionSyncError, match="limited to"):
        store.push("device-one", "batch-000000000100", [{
            "namespace": "preferences", "item_id": "large", "base_version": 0,
            "payload": {"value": "x" * 9000},
        }])


def test_sync_store_rejects_ahead_cursor_and_limits_pull(tmp_path):
    store = CompanionSyncStore(tmp_path / "sync.sqlite3")
    with pytest.raises(CompanionSyncError, match="ahead"):
        store.pull("device-one", since=1)
    with pytest.raises(CompanionSyncError, match="limit"):
        store.pull("device-one", limit=101)
