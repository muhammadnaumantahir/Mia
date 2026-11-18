from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeTranscriber:
    model_size = "test-model"

    def transcribe(self, audio_bytes, *, filename="recording.webm", language=None):
        assert audio_bytes == b"fake-webm-audio"
        assert filename == "recording.webm"
        assert language is None
        return {
            "text": "Hello Mia",
            "language": "en",
            "language_probability": 0.99,
        }


def test_voice_transcription_is_local_and_audio_is_not_retained(tmp_path):
    store = ObservatoryStore(tmp_path / "voice.sqlite3")
    client = TestClient(
        create_app(store=store, transcriber=FakeTranscriber())
    )
    response = client.post(
        "/api/voice/transcribe?filename=recording.webm",
        content=b"fake-webm-audio",
        headers={"content-type": "audio/webm"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "Hello Mia"
    assert payload["language"] == "en"
    assert payload["audio_retained"] is False
    event = store.list_events()[0]
    assert event["category"] == "voice"
    assert '"audio_retained": false' in event["payload_json"]


def test_voice_endpoint_rejects_non_audio_content(tmp_path):
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "voice.sqlite3"),
            transcriber=FakeTranscriber(),
        )
    )
    response = client.post(
        "/api/voice/transcribe",
        content=b"not-audio",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 415
