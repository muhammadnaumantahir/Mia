"""MIA-M1-11: STT language validation, latency reporting and text-language fixtures.

These tests use fake engines and plain text. They say nothing about real speech
recognition or synthesis quality (OWNER_VERIFICATION_REQUIRED).
"""

import json

import pytest
from fastapi.testclient import TestClient

from mia.gateway.language import detect_language
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.voice.stt import FasterWhisperTranscriber, normalize_stt_language


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None), ("", None), ("  ", None), ("auto", None), ("AUTO", None),
        ("en", "en"), ("en-US", "en"), ("EN_gb", "en"),
        ("ur", "ur"), ("ur-PK", "ur"), ("ur_PK", "ur"),
    ],
)
def test_normalize_accepts_supported_languages(raw, expected):
    assert normalize_stt_language(raw) == expected


@pytest.mark.parametrize("raw", ["fr", "hi", "roman-urdu", "../etc", "e n", "english", "en;ur", "123"])
def test_normalize_rejects_unsupported_or_malformed(raw):
    with pytest.raises(ValueError):
        normalize_stt_language(raw)


class RecordingTranscriber:
    model_size = "test-model"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio_bytes, *, filename="recording.webm", language=None):
        self.calls.append(language)
        return {"text": "salam", "language": "ur", "language_probability": 0.9}


class FakeSynth:
    available = True

    def list_voices(self):
        return []

    def synthesize(self, text, *, voice_id=None, rate=175):
        return b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 40


def _client(tmp_path, transcriber=None):
    store = ObservatoryStore(tmp_path / "v.sqlite3")
    app = create_app(store=store, transcriber=transcriber or RecordingTranscriber(), tts_synthesizer=FakeSynth())
    return TestClient(app), store


def _post(client, query):
    return client.post(
        "/api/voice/transcribe" + query, content=b"audio", headers={"content-type": "audio/webm"}
    )


@pytest.mark.parametrize(
    ("query", "expected"),
    [("", None), ("?language=auto", None), ("?language=en-US", "en"), ("?language=ur-PK", "ur")],
)
def test_transcribe_forwards_only_normalized_language(tmp_path, query, expected):
    transcriber = RecordingTranscriber()
    client, _ = _client(tmp_path, transcriber)
    response = _post(client, query)
    assert response.status_code == 200
    assert transcriber.calls == [expected]
    assert response.json()["requested_language"] == expected


@pytest.mark.parametrize("bad", ["fr", "xx", "..%2Fetc", "en%3Bur"])
def test_transcribe_rejects_unsupported_language_before_engine_runs(tmp_path, bad):
    transcriber = RecordingTranscriber()
    client, store = _client(tmp_path, transcriber)
    response = _post(client, f"?language={bad}")
    assert response.status_code == 400
    assert transcriber.calls == []
    assert store.list_events() == []


def test_transcribe_reports_latency_in_response_and_event(tmp_path):
    client, store = _client(tmp_path)
    body = _post(client, "?language=ur").json()
    assert isinstance(body["latency_ms"], float) and body["latency_ms"] >= 0
    payload = json.loads(store.list_events()[0]["payload_json"])
    assert payload["latency_ms"] == body["latency_ms"]
    assert payload["requested_language"] == "ur"
    assert payload["audio_retained"] is False


def test_speak_reports_latency_header_and_event_without_logging_text(tmp_path):
    client, store = _client(tmp_path)
    response = client.post("/api/voice/speak", json={"text": "secret words"})
    assert response.status_code == 200
    latency = float(response.headers["x-mia-latency-ms"])
    assert latency >= 0
    event = store.list_events()[0]
    assert json.loads(event["payload_json"])["latency_ms"] == latency
    assert "secret words" not in event["payload_json"]


def test_failed_transcription_records_no_latency_event(tmp_path):
    class Broken(RecordingTranscriber):
        def transcribe(self, *a, **k):
            raise RuntimeError("engine down")

    client, store = _client(tmp_path, Broken())
    assert _post(client, "").status_code == 503
    assert store.list_events() == []


def test_adapter_consumes_segments_before_deleting_audio_and_forwards_language(tmp_path, monkeypatch):
    seen = {}

    class Seg:
        def __init__(self, text):
            self.text = text

    class Info:
        language = "ur"
        language_probability = 0.8

    class Model:
        def transcribe(self, path, **kwargs):
            import os
            seen["language"] = kwargs["language"]

            def gen():
                seen["exists_while_streaming"] = os.path.exists(path)
                yield Seg(" پہلا ")
                yield Seg("   ")
                yield Seg("دوسرا")

            seen["path"] = path
            return gen(), Info()

    t = FasterWhisperTranscriber(data_dir=tmp_path)
    monkeypatch.setattr(t, "_get_model", lambda: Model())
    result = t.transcribe(b"x", filename="a.wav", language="ur")
    assert result["text"] == "پہلا دوسرا"
    assert seen["language"] == "ur" and seen["exists_while_streaming"] is True
    import os
    assert not os.path.exists(seen["path"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("What is on my schedule tomorrow?", "en"),
        ("Please remind me about the project review", "en"),
        ("mujhe kal ka schedule bata do", "roman_urdu"),
        ("aap kaise hain, kya haal hai", "roman_urdu"),
        ("mera kaam abhi nahi hua lekin kal karo", "roman_urdu"),
        ("مجھے کل کا شیڈول بتاؤ", "urdu_script"),
        ("آپ کیسے ہیں", "urdu_script"),
        ("", "en"),
        ("   ", "en"),
        ("1234 5678", "en"),
    ],
)
def test_text_language_fixtures(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize("text", ["I think this is fine", "The cat is on the mat and that is all", "Is it ok"])
def test_plain_english_is_not_misread_as_roman_urdu(text):
    assert detect_language(text) == "en"
