"""Optional offline text-to-speech using installed operating-system voices."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Protocol


class SpeechSynthesisError(RuntimeError):
    """The local speech synthesizer is missing or failed."""


class SpeechSynthesizer(Protocol):
    @property
    def available(self) -> bool:
        """Whether the optional local TTS dependency is installed."""

    def list_voices(self) -> list[dict[str, Any]]:
        """Return actual system voices and their metadata."""

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        rate: int = 175,
    ) -> bytes:
        """Return a short WAV audio clip without retaining it."""


class Pyttsx3SpeechSynthesizer:
    """Generate offline WAV audio with the host's installed system voices.

    Engine creation, voice selection, synthesis and event-loop pumping happen
    in the same calling thread. Calls are serialized because the underlying
    SAPI driver is not designed for concurrent use by multiple request threads.
    """

    MAX_TEXT_CHARACTERS = 3000
    MAX_AUDIO_BYTES = 15 * 1024 * 1024

    def __init__(
        self,
        *,
        engine_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._engine_factory = engine_factory
        self._lock = Lock()

    @property
    def available(self) -> bool:
        if self._engine_factory is not None:
            return True
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            return False
        return True

    def _engine(self):
        if self._engine_factory is not None:
            try:
                return self._engine_factory()
            except Exception as exc:
                raise SpeechSynthesisError(
                    "The configured speech engine could not be started"
                ) from exc
        try:
            import pyttsx3
        except ImportError as exc:
            raise SpeechSynthesisError(
                'Install the optional local TTS extra: pip install -e ".[tts]"'
            ) from exc
        try:
            if os.name == "nt":
                return pyttsx3.init(driverName="sapi5")
            return pyttsx3.init()
        except Exception as exc:
            raise SpeechSynthesisError(
                "The operating-system speech engine could not be started; "
                "check installed voices and audio services"
            ) from exc

    @staticmethod
    def _voice_metadata(voice: Any) -> dict[str, Any]:
        raw_languages = getattr(voice, "languages", None) or []
        languages: list[str] = []
        for language in raw_languages:
            if isinstance(language, bytes):
                language = language.decode("utf-8", errors="ignore")
            normalized = "".join(
                character for character in str(language)
                if character.isprintable()
            ).strip()
            if normalized:
                languages.append(normalized)
        raw_gender = getattr(voice, "gender", None)
        gender = str(raw_gender).strip().lower() if raw_gender else None
        if gender not in {"female", "male", "neutral"}:
            gender = None
        return {
            "id": str(getattr(voice, "id", "")),
            "name": str(getattr(voice, "name", None) or "System voice"),
            "gender": gender,
            "languages": languages,
        }

    def list_voices(self) -> list[dict[str, Any]]:
        if not self.available:
            raise SpeechSynthesisError(
                'Install the optional local TTS extra: pip install -e ".[tts]"'
            )
        with self._lock:
            engine = self._engine()
            try:
                voices = engine.getProperty("voices") or []
                return [
                    metadata for voice in voices
                    if (metadata := self._voice_metadata(voice))["id"]
                ]
            except Exception as exc:
                raise SpeechSynthesisError(
                    "The operating-system speech engine could not list voices"
                ) from exc
            finally:
                try:
                    engine.stop()
                except Exception:
                    pass

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        rate: int = 175,
    ) -> bytes:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech text must not be empty")
        if len(text) > self.MAX_TEXT_CHARACTERS:
            raise ValueError(
                f"Speech text exceeds {self.MAX_TEXT_CHARACTERS} characters"
            )
        if not 100 <= rate <= 220:
            raise ValueError("Speech rate must be between 100 and 220 words per minute")
        if not self.available:
            raise SpeechSynthesisError(
                'Install the optional local TTS extra: pip install -e ".[tts]"'
            )

        with self._lock:
            engine = self._engine()
            audio_path: Path | None = None
            try:
                voices = engine.getProperty("voices") or []
                if voice_id:
                    selected_voice = next(
                        (voice for voice in voices if str(getattr(voice, "id", "")) == voice_id),
                        None,
                    )
                    if selected_voice is None:
                        raise ValueError("The selected system voice is not installed")
                    engine.setProperty("voice", selected_voice.id)
                engine.setProperty("rate", rate)
                with tempfile.NamedTemporaryFile(
                    prefix="mia-tts-",
                    suffix=".wav",
                    delete=False,
                ) as output:
                    audio_path = Path(output.name)
                audio_path.unlink(missing_ok=True)
                engine.save_to_file(text, str(audio_path))
                engine.runAndWait()
                if not audio_path.is_file():
                    raise SpeechSynthesisError("The system speech engine produced no audio")
                if audio_path.stat().st_size > self.MAX_AUDIO_BYTES:
                    raise SpeechSynthesisError("Generated speech audio exceeds 15 MB")
                audio = audio_path.read_bytes()
                if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
                    raise SpeechSynthesisError("The system speech engine returned an invalid WAV")
                return audio
            except ValueError:
                raise
            except SpeechSynthesisError:
                raise
            except Exception as exc:
                raise SpeechSynthesisError(
                    "The operating-system speech engine could not synthesize audio"
                ) from exc
            finally:
                if audio_path is not None:
                    audio_path.unlink(missing_ok=True)
                try:
                    engine.stop()
                except Exception:
                    pass
