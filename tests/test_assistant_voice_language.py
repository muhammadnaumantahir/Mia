# ruff: noqa: F811
"""MIA-LANG-02: the Assistant's "Auto - match language" local voice option (Chromium).

Reuses the real server/page fixtures from test_voice_browser; the voice API routes are mocked
because no real system voices exist in CI. Nothing here proves real Urdu speech quality.
"""

import io
import json
import wave

from tests.test_voice_browser import FAKES, browser, ctx, pw, server  # noqa: F401

VOICES = {
    "engine": "pyttsx3-local",
    "audio_retained": False,
    "voices": [
        {"id": "en-voice", "name": "Zira", "gender": "female", "languages": ["en-US"]},
    ],
}


def _wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(b"\x00\x00" * 1600)
    return buffer.getvalue()


def _install(ctx, *, urdu: bool, speak_headers=None):
    voices = json.loads(json.dumps(VOICES))
    if urdu:
        voices["voices"].append({"id": "ur-voice", "name": "Uzma", "gender": "female", "languages": ["ur-PK"]})
    status = {"local_tts_languages": {"en": True, "ur": urdu}}
    bodies: list[dict] = []
    ctx.page.route(
        "**/api/voice/tts/voices",
        lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(voices)),
    )
    ctx.page.route(
        "**/api/voice/status",
        lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(status)),
    )

    def speak(route):
        bodies.append(json.loads(route.request.post_data or "{}"))
        route.fulfill(
            status=200, body=_wav(), headers={"content-type": "audio/wav", **(speak_headers or {})}
        )

    ctx.page.route("**/api/voice/speak", speak)
    ctx.js(
        "(()=>{window.__hints=[];const h=document.getElementById('hint');"
        "new MutationObserver(()=>window.__hints.push(h.textContent)).observe(h,"
        "{childList:true,characterData:true,subtree:true});})()"
    )
    ctx.js("refreshVoices()")
    ctx.wait("Array.from(document.getElementById('voiceChoice').options).some(o=>o.value==='local-auto')")
    return bodies


def _speak(ctx, text):
    ctx.js(f"speakReply({text!r},{{force:true}})")


def test_auto_option_is_offered_and_sends_language_not_voice_id(ctx):
    bodies = _install(ctx, urdu=True)
    label = ctx.js("Array.from(document.getElementById('voiceChoice').options).find(o=>o.value==='local-auto').textContent")
    assert label == "Auto · match language"
    ctx.js("document.getElementById('voiceChoice').value='local-auto'")
    _speak(ctx, "میں ٹھیک ہوں۔")
    deadline = 40
    while not bodies and deadline:
        ctx.sleep(0.1)
        deadline -= 1
    assert bodies, "no /api/voice/speak request was made"
    assert bodies[0]["language"] == "auto"
    assert "voice_id" not in bodies[0]


def test_option_label_says_when_no_urdu_voice_is_installed(ctx):
    _install(ctx, urdu=False)
    label = ctx.js("Array.from(document.getElementById('voiceChoice').options).find(o=>o.value==='local-auto').textContent")
    assert "no Urdu voice installed" in label


def test_explicit_local_voice_still_sends_voice_id_without_language(ctx):
    bodies = _install(ctx, urdu=True)
    ctx.js("document.getElementById('voiceChoice').value='local:en-voice'")
    _speak(ctx, "Hello there.")
    deadline = 40
    while not bodies and deadline:
        ctx.sleep(0.1)
        deadline -= 1
    assert bodies and bodies[0]["voice_id"] == "en-voice"
    assert "language" not in bodies[0]


def test_no_matching_voice_is_shown_to_the_owner_not_hidden(ctx):
    bodies = _install(ctx, urdu=False, speak_headers={"X-Mia-Voice-Selection": "no_matching_voice"})
    ctx.js("document.getElementById('voiceChoice').value='local-auto'")
    _speak(ctx, "میں ٹھیک ہوں۔")
    ctx.wait("window.__hints.some(h=>h.includes('No installed local voice matches'))", timeout=8000)
    assert bodies


def test_roman_urdu_approximation_is_disclosed(ctx):
    _install(ctx, urdu=True, speak_headers={"X-Mia-Voice-Selection": "matched", "X-Mia-Voice-Approximate": "true"})
    ctx.js("document.getElementById('voiceChoice').value='local-auto'")
    _speak(ctx, "mujhe kaam hai aur aap kaise hain")
    ctx.wait("window.__hints.some(h=>h.includes('pronunciation is approximate'))", timeout=8000)
