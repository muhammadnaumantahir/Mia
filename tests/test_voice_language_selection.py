import json

import pytest
from fastapi.testclient import TestClient

from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore
from mia.voice.voice_select import (
    available_tts_languages,
    choose_voice,
    normalize_tts_language,
    tts_language_for_text,
    voice_matches_language,
)

EN = {"id": "en-voice", "name": "Zira", "gender": "female", "languages": ["en-US"]}
UR = {"id": "ur-voice", "name": "Uzma", "gender": "female", "languages": ["ur-PK"]}
HI = {"id": "hi-voice", "name": "Kalpana", "gender": "female", "languages": ["hi-IN"]}
SAPI_UR = {
    "id": r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_UR-PK_UZMA_11.0",
    "name": "System voice",
    "gender": None,
    "languages": [],
}
NAMED_UR = {"id": "x1", "name": "Microsoft Asad - Urdu (Pakistan)", "gender": None, "languages": []}

URDU_TEXT = "میں ٹھیک ہوں، آپ کیسے ہیں؟"
ROMAN_URDU = "mujhe kaam hai aur aap kaise hain"


def test_matches_language_tags_ids_and_names_but_not_other_languages():
    assert voice_matches_language(UR, "ur") and not voice_matches_language(UR, "en")
    assert voice_matches_language(SAPI_UR, "ur")
    assert voice_matches_language(NAMED_UR, "ur")
    assert voice_matches_language({"id": "e", "name": "x", "languages": ["en_GB"]}, "en")
    # Hindi, Arabic and Persian voices are never an Urdu voice.
    assert not voice_matches_language(HI, "ur")
    assert not voice_matches_language({"id": "a", "name": "Arabic", "languages": ["ar-SA"]}, "ur")
    assert not voice_matches_language({"id": "u", "name": "Louis", "languages": []}, "ur")
    assert not voice_matches_language(UR, "fr")


def test_urdu_script_picks_urdu_voice_and_english_picks_english():
    voices = [EN, HI, UR]
    ur = choose_voice(voices, language="auto", text=URDU_TEXT)
    assert (ur.voice_id, ur.language, ur.status, ur.approximate) == ("ur-voice", "ur", "matched", False)
    en = choose_voice(voices, language="auto", text="How are you today?")
    assert (en.voice_id, en.language, en.status) == ("en-voice", "en", "matched")


def test_roman_urdu_uses_english_voice_and_is_marked_approximate():
    assert tts_language_for_text(ROMAN_URDU) == ("en", True)
    choice = choose_voice([UR, EN], language="auto", text=ROMAN_URDU)
    assert choice.voice_id == "en-voice" and choice.approximate is True


def test_no_matching_voice_is_reported_not_substituted():
    choice = choose_voice([EN, HI], language="auto", text=URDU_TEXT)
    assert choice.voice_id is None and choice.status == "no_matching_voice"
    assert choice.language == "ur"
    assert choose_voice([], language="en").status == "no_matching_voice"


def test_voices_without_ids_are_ignored_and_first_match_wins():
    other = {**UR, "id": "ur-second"}
    assert choose_voice([{"id": "", "languages": ["ur"]}, UR, other], language="ur").voice_id == "ur-voice"


def test_available_languages_reflect_installed_voices_only():
    assert available_tts_languages([EN, HI]) == {"en": True, "ur": False}
    assert available_tts_languages([]) == {"en": False, "ur": False}
    assert available_tts_languages([EN, SAPI_UR]) == {"en": True, "ur": True}


def test_normalize_language():
    assert normalize_tts_language(None) is None and normalize_tts_language("  ") is None
    assert normalize_tts_language("UR-PK") == "ur" and normalize_tts_language("en_US") == "en"
    assert normalize_tts_language("auto") == "auto"
    with pytest.raises(ValueError):
        normalize_tts_language("fr")


class RecordingSynthesizer:
    available = True

    def __init__(self, voices):
        self.voices = voices
        self.calls = []

    def list_voices(self):
        return self.voices

    def synthesize(self, text, *, voice_id=None, rate=175):
        self.calls.append(voice_id)
        return b"RIFF-fake-wav-data"


def _client(tmp_path, voices):
    store = ObservatoryStore(tmp_path / "lang.sqlite3")
    synth = RecordingSynthesizer(voices)
    return TestClient(create_app(store=store, tts_synthesizer=synth)), synth, store


def test_speak_auto_selects_urdu_voice_and_records_selection(tmp_path):
    client, synth, store = _client(tmp_path, [EN, UR])
    response = client.post("/api/voice/speak", json={"text": URDU_TEXT, "language": "auto"})
    assert response.status_code == 200
    assert synth.calls == ["ur-voice"]
    assert response.headers["x-mia-voice-selection"] == "matched"
    assert response.headers["x-mia-voice-language"] == "ur"
    assert "x-mia-voice-approximate" not in response.headers
    payload = json.loads(store.list_events()[0]["payload_json"])
    assert payload["voice_selection"] == "matched" and payload["voice_language"] == "ur"
    assert URDU_TEXT not in store.list_events()[0]["payload_json"]


def test_speak_missing_urdu_voice_falls_back_honestly(tmp_path):
    client, synth, _ = _client(tmp_path, [EN])
    response = client.post("/api/voice/speak", json={"text": URDU_TEXT, "language": "ur"})
    assert response.status_code == 200
    assert synth.calls == [None]
    assert response.headers["x-mia-voice-selection"] == "no_matching_voice"


def test_speak_roman_urdu_is_flagged_approximate(tmp_path):
    client, synth, _ = _client(tmp_path, [UR, EN])
    response = client.post("/api/voice/speak", json={"text": ROMAN_URDU, "language": "auto"})
    assert synth.calls == ["en-voice"]
    assert response.headers["x-mia-voice-approximate"] == "true"


def test_explicit_voice_id_wins_over_language_and_skips_listing(tmp_path):
    client, synth, _ = _client(tmp_path, [EN, UR])
    response = client.post(
        "/api/voice/speak", json={"text": URDU_TEXT, "language": "ur", "voice_id": "en-voice"}
    )
    assert synth.calls == ["en-voice"]
    assert response.headers["x-mia-voice-selection"] == "explicit"


def test_no_language_keeps_previous_default_behaviour(tmp_path):
    client, synth, _ = _client(tmp_path, [EN, UR])
    response = client.post("/api/voice/speak", json={"text": URDU_TEXT})
    assert synth.calls == [None]
    assert response.headers["x-mia-voice-selection"] == "default"
    assert "x-mia-voice-language" not in response.headers


def test_unsupported_language_is_rejected_before_synthesis(tmp_path):
    client, synth, store = _client(tmp_path, [EN, UR])
    response = client.post("/api/voice/speak", json={"text": "hello", "language": "fr"})
    assert response.status_code == 400
    assert synth.calls == [] and store.list_events() == []


def test_status_reports_installed_voice_languages(tmp_path):
    client, _, _ = _client(tmp_path, [EN, HI])
    status = client.get("/api/voice/status").json()
    assert status["local_tts_languages"] == {"en": True, "ur": False}
    assert status["local_tts_voice_count"] == 2
