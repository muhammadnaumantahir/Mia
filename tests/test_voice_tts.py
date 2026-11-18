import io
import wave
from pathlib import Path

import pytest

from mia.voice.tts import Pyttsx3SpeechSynthesizer, SpeechSynthesisError


def _minimal_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 160)
    return buffer.getvalue()


class FakeVoice:
    def __init__(self, voice_id, name, gender, languages):
        self.id = voice_id
        self.name = name
        self.gender = gender
        self.languages = languages


class FakeEngine:
    def __init__(self):
        self.voices = [
            FakeVoice("female-en", "Female English", "female", ["en-US"]),
            FakeVoice("unknown", "Unknown voice", None, []),
        ]
        self.properties = {}
        self.saved_text = None
        self.stopped = False

    def getProperty(self, name):
        return self.voices if name == "voices" else self.properties.get(name)

    def setProperty(self, name, value):
        self.properties[name] = value

    def save_to_file(self, text, filename):
        self.saved_text = text
        Path(filename).write_bytes(_minimal_wav())

    def runAndWait(self):
        return None

    def stop(self):
        self.stopped = True


def test_voice_metadata_uses_actual_gender_and_language_values():
    engine = FakeEngine()
    synthesizer = Pyttsx3SpeechSynthesizer(engine_factory=lambda: engine)

    voices = synthesizer.list_voices()

    assert voices[0] == {
        "id": "female-en",
        "name": "Female English",
        "gender": "female",
        "languages": ["en-US"],
    }
    assert voices[1]["gender"] is None
    assert engine.stopped is True


def test_synthesizer_uses_selected_installed_voice_and_deletes_wav():
    engine = FakeEngine()
    synthesizer = Pyttsx3SpeechSynthesizer(engine_factory=lambda: engine)

    result = synthesizer.synthesize("Hello Mia", voice_id="female-en", rate=180)

    assert result[:4] == b"RIFF"
    assert engine.saved_text == "Hello Mia"
    assert engine.properties["voice"] == "female-en"
    assert engine.properties["rate"] == 180
    assert engine.stopped is True


def test_synthesizer_rejects_unknown_voice_and_invalid_limits():
    synthesizer = Pyttsx3SpeechSynthesizer(engine_factory=FakeEngine)

    with pytest.raises(ValueError, match="not installed"):
        synthesizer.synthesize("Hello", voice_id="missing")
    with pytest.raises(ValueError, match="rate"):
        synthesizer.synthesize("Hello", rate=250)
    with pytest.raises(ValueError, match="characters"):
        synthesizer.synthesize("x" * 3001)


def test_synthesizer_rejects_invalid_wav():
    class InvalidEngine(FakeEngine):
        def save_to_file(self, text, filename):
            Path(filename).write_bytes(b"not a wav")

    synthesizer = Pyttsx3SpeechSynthesizer(engine_factory=InvalidEngine)

    with pytest.raises(SpeechSynthesisError, match="invalid WAV"):
        synthesizer.synthesize("Hello")


def test_synthesizer_wraps_system_driver_startup_failures():
    def failed_engine():
        raise OSError("SAPI driver unavailable")

    synthesizer = Pyttsx3SpeechSynthesizer(engine_factory=failed_engine)

    with pytest.raises(SpeechSynthesisError, match="could not be started"):
        synthesizer.list_voices()
