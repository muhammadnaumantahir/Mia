"""Language-aware choice of an installed local TTS voice (MIA-LANG-01).

Pure functions over the voice metadata that ``Pyttsx3SpeechSynthesizer.list_voices``
returns. Nothing here starts an engine or touches audio.

Honest limits, by design:

* Urdu script is spoken by an installed Urdu voice only. Hindi, Arabic or Persian voices
  are never substituted: they cannot read Urdu script correctly.
* Roman Urdu has no TTS language of its own. It is routed to an English voice, which
  reads it with English phonetics. That is an approximation and is reported as such.
* If no voice matches, no voice is chosen (``voice_id`` is ``None``) and the status says
  so; the caller then uses the system default and must not claim a language match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mia.gateway.language import detect_language

SUPPORTED_TTS_LANGUAGES = ("en", "ur")
_REQUESTABLE = frozenset({"auto", *SUPPORTED_TTS_LANGUAGES})

# Windows SAPI ids look like ...\TTS_MS_UR-PK_...; names read "... - Urdu (Pakistan)".
_ID_TAG = {
    "en": re.compile(r"(?<![a-z])en[-_][a-z]{2}(?![a-z])", re.IGNORECASE),
    "ur": re.compile(r"(?<![a-z])ur[-_][a-z]{2}(?![a-z])", re.IGNORECASE),
}
_NAME_WORD = {
    "en": re.compile(r"\benglish\b", re.IGNORECASE),
    "ur": re.compile(r"\burdu\b", re.IGNORECASE),
}


@dataclass(frozen=True)
class VoiceChoice:
    voice_id: str | None
    language: str
    # "matched": an installed voice for this language was chosen.
    # "no_matching_voice": none installed; caller must use the system default.
    status: str
    # True when the text itself is Roman Urdu spoken by an English voice (approximation).
    approximate: bool = False


def normalize_tts_language(value: str | None) -> str | None:
    """Return ``auto``/``en``/``ur`` or ``None`` when no language was requested."""
    if value is None:
        return None
    cleaned = str(value).strip().lower().replace("_", "-")
    if not cleaned:
        return None
    primary = cleaned.split("-", 1)[0]
    if primary in _REQUESTABLE:
        return primary
    raise ValueError("Speech language must be auto, en or ur")


def tts_language_for_text(text: str) -> tuple[str, bool]:
    """Map text to a TTS language and whether the mapping is an approximation."""
    detected = detect_language(text)
    if detected == "urdu_script":
        return "ur", False
    if detected == "roman_urdu":
        return "en", True
    return "en", False


def voice_matches_language(voice: dict[str, Any], language: str) -> bool:
    if language not in SUPPORTED_TTS_LANGUAGES:
        return False
    for tag in voice.get("languages") or []:
        primary = re.split(r"[-_]", str(tag).strip().lower(), maxsplit=1)[0]
        if primary == language:
            return True
    if _ID_TAG[language].search(str(voice.get("id") or "")):
        return True
    return bool(_NAME_WORD[language].search(str(voice.get("name") or "")))


def available_tts_languages(voices: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        language: any(voice_matches_language(voice, language) for voice in voices)
        for language in SUPPORTED_TTS_LANGUAGES
    }


def choose_voice(
    voices: list[dict[str, Any]],
    *,
    language: str,
    text: str = "",
) -> VoiceChoice:
    """Pick the first installed voice for ``language`` (``auto`` detects from ``text``)."""
    approximate = False
    if language == "auto":
        language, approximate = tts_language_for_text(text)
    elif language not in SUPPORTED_TTS_LANGUAGES:
        raise ValueError("Speech language must be auto, en or ur")
    for voice in voices:
        if voice.get("id") and voice_matches_language(voice, language):
            return VoiceChoice(str(voice["id"]), language, "matched", approximate)
    return VoiceChoice(None, language, "no_matching_voice", approximate)
