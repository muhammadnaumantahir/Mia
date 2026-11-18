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


class FakeSynthesizer:
    available = True

    def list_voices(self):
        return [
            {
                "id": "fake-female",
                "name": "Mia Test Voice",
                "gender": "female",
                "languages": ["en-US"],
            },
            {
                "id": "unknown-gender",
                "name": "System Voice",
                "gender": None,
                "languages": [],
            },
        ]

    def synthesize(self, text, *, voice_id=None, rate=175):
        assert text == "Hello Mia"
        assert voice_id == "fake-female"
        assert rate == 175
        return b"RIFF-fake-wav-data"


def test_local_tts_lists_actual_voice_metadata(tmp_path):
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "local-tts.sqlite3"),
            transcriber=FakeTranscriber(),
            tts_synthesizer=FakeSynthesizer(),
        )
    )

    response = client.get("/api/voice/tts/voices")

    assert response.status_code == 200
    voices = response.json()["voices"]
    assert voices[0]["gender"] == "female"
    assert voices[1]["gender"] is None
    assert response.json()["audio_retained"] is False


def test_local_tts_returns_wav_and_does_not_log_spoken_text(tmp_path):
    store = ObservatoryStore(tmp_path / "local-tts.sqlite3")
    client = TestClient(
        create_app(
            store=store,
            transcriber=FakeTranscriber(),
            tts_synthesizer=FakeSynthesizer(),
        )
    )

    response = client.post(
        "/api/voice/speak",
        json={"text": "Hello Mia", "voice_id": "fake-female", "rate": 175},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content == b"RIFF-fake-wav-data"
    event = store.list_events()[0]
    assert event["event_type"] == "speech_synthesis"
    assert '"text_characters": 9' in event["payload_json"]
    assert "Hello Mia" not in event["payload_json"]
    assert '"audio_retained": false' in event["payload_json"]


def test_local_tts_rejects_empty_or_oversized_text(tmp_path):
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "local-tts.sqlite3"),
            transcriber=FakeTranscriber(),
            tts_synthesizer=FakeSynthesizer(),
        )
    )

    empty = client.post("/api/voice/speak", json={"text": ""})
    oversized = client.post("/api/voice/speak", json={"text": "x" * 3001})

    assert empty.status_code == 422
    assert oversized.status_code == 422


def test_voice_status_reports_local_tts_capability(tmp_path):
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "voice-status.sqlite3"),
            transcriber=FakeTranscriber(),
            tts_synthesizer=FakeSynthesizer(),
        )
    )

    response = client.get("/api/voice/status")

    assert response.status_code == 200
    assert response.json()["local_text_to_speech"] == "ready"
    assert response.json()["local_tts_voice_count"] == 2
    assert response.json()["always_listening"] is False


def test_local_tts_engine_startup_failure_is_reported_as_unavailable(tmp_path):
    class UnavailableSynthesizer:
        available = False

        def list_voices(self):
            from mia.voice.tts import SpeechSynthesisError

            raise SpeechSynthesisError("speech engine unavailable")

        def synthesize(self, text, *, voice_id=None, rate=175):
            from mia.voice.tts import SpeechSynthesisError

            raise SpeechSynthesisError("speech engine unavailable")

    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "unavailable-tts.sqlite3"),
            transcriber=FakeTranscriber(),
            tts_synthesizer=UnavailableSynthesizer(),
        )
    )
    voices = client.get("/api/voice/tts/voices")

    assert voices.status_code == 503
    assert "speech engine unavailable" in voices.json()["detail"]

    status = client.get("/api/voice/status")
    assert status.status_code == 200
    assert status.json()["local_text_to_speech"] == "install_tts_extra"



def test_voice_status_distinguishes_missing_system_voices(tmp_path):
    class EmptyVoiceSynthesizer(FakeSynthesizer):
        def list_voices(self):
            return []

    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "no-system-voices.sqlite3"),
            transcriber=FakeTranscriber(),
            tts_synthesizer=EmptyVoiceSynthesizer(),
        )
    )
    response = client.get("/api/voice/status")

    assert response.status_code == 200
    assert response.json()["local_text_to_speech"] == "no_system_voices"
    assert response.json()["text_to_speech"] == "browser_speech_synthesis"
