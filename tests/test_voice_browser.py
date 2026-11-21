"""Real-browser (Chromium via Playwright) tests of the voice session lifecycle.

Speech recognition and speech synthesis are replaced by controllable fakes injected before the page
loads, so these tests exercise Mia's real page JavaScript, the real FastAPI app, the real model
gateway (with a recording provider), conversation context and memory retrieval. They do NOT prove that
a physical microphone or speaker works; see docs/voice/OWNER_VERIFICATION.md.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn

from mia.gateway.contracts import ModelCandidate, ModelRequest, StreamChunk, TokenUsage
from mia.gateway.registry import ProviderRegistry
from mia.gateway.service import ModelGateway
from mia.memory.store import MemoryStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")

FAKES = r"""
(() => {
  const rec = window.__rec = {instances: [], autoEnd: false, noApi: false};
  class FakeRecognition {
    constructor() { this.active = false; this.lang = ''; rec.instances.push(this); }
    start() {
      this.active = true;
      setTimeout(() => { this.onstart && this.onstart(); if (rec.autoEnd) this._end(); }, 5);
    }
    stop() { this._end(); }
    abort() { this.active = false; }
    _end() {
      if (!this.active) return;
      this.active = false;
      setTimeout(() => this.onend && this.onend(), 5);
    }
  }
  if (new URLSearchParams(location.search).has('norec')) {
    window.SpeechRecognition = undefined;
    window.webkitSpeechRecognition = undefined;
  } else {
    window.SpeechRecognition = FakeRecognition;
    window.webkitSpeechRecognition = FakeRecognition;
  }
  rec.live = () => rec.instances.filter(i => i.active);
  rec.emit = (text, isFinal) => {
    const target = rec.live().reverse().find(i => i.onresult);
    if (!target) return false;
    const result = [{transcript: text}]; result.isFinal = isFinal;
    target.onresult({resultIndex: 0, results: [result]});
    return true;
  };
  rec.error = code => { const t = rec.live().reverse().find(i => i.onerror); t && t.onerror({error: code}); };

  const tts = window.__tts = {spoken: [], cancelled: 0, duration: 120, current: null, timer: null, overlap: 0};
  class FakeUtterance { constructor(text) { this.text = text; this.rate = 1; this.volume = 1; } }
  window.SpeechSynthesisUtterance = FakeUtterance;
  const synth = {
    onvoiceschanged: null,
    getVoices: () => [],
    speak(u) {
      if (tts.current) tts.overlap += 1;
      tts.current = u;
      tts.spoken.push({text: u.text, rate: u.rate, volume: u.volume});
      tts.timer = setTimeout(() => { const c = tts.current; tts.current = null; c.onend && c.onend(); }, tts.duration);
    },
    cancel() {
      clearTimeout(tts.timer);
      const c = tts.current; tts.current = null;
      if (c) { tts.cancelled += 1; c.onerror && c.onerror({error: 'interrupted'}); }
    },
  };
  Object.defineProperty(window, 'speechSynthesis', {value: synth, configurable: true});
})();
"""


class Provider:
    provider_id = "local-test"
    data_local = True

    def __init__(self):
        self.reply = "Noted."
        self.delay = 0.0
        self.requests: list[ModelRequest] = []

    async def health(self):
        return True

    async def list_models(self):
        return [
            ModelCandidate(
                provider_id=self.provider_id,
                model_id="model",
                capabilities=frozenset({"chat"}),
                context_window=8192,
                free_tier=True,
                healthy=True,
                enabled=True,
                data_local=True,
            )
        ]

    async def complete(self, candidate, request: ModelRequest):
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        return self.reply

    async def stream(self, candidate, request: ModelRequest):
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        yield StreamChunk(delta=self.reply)
        yield StreamChunk(finish_reason="stop", usage=TokenUsage(5, 2))

    def user_prompts(self):
        return [r.messages[-1]["content"] for r in self.requests]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("voice-browser")
    import os

    os.environ["MIA_DATA_DIR"] = str(tmp / "data")
    provider = Provider()
    registry = ProviderRegistry()
    registry.register(provider)
    memory = MemoryStore(tmp / "memory.sqlite3")
    memory.add_item(
        kind="fact",
        title="Billing database decision",
        content="We decided to use PostgreSQL for the billing service for transactional safety.",
        source_title="Architecture review",
        trust_level="user",
    )
    app = create_app(
        store=ObservatoryStore(tmp / "obs.sqlite3"),
        gateway=ModelGateway(registry),
        memory=memory,
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uv = uvicorn.Server(config)
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    assert uv.started
    yield f"http://127.0.0.1:{port}", provider
    uv.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def pw():
    with playwright_sync.sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Chromium is not available for Playwright: {exc}")
    yield b
    b.close()


class Ctx:
    def __init__(self, page, provider, base):
        self.page, self.provider, self.base = page, provider, base
        self.requests: list[str] = []

    def js(self, expr):
        return self.page.evaluate(expr)

    def wait_state(self, state, timeout=10000):
        try:
            self.wait(f"document.getElementById('voiceOrb').dataset.state==={state!r}", timeout=timeout)
        except Exception as exc:
            raise AssertionError(
                f"wanted state {state!r}; got {self.state()!r} / {self.status()!r} / "
                f"mic calls {self.js('window.__gum ? window.__gum.calls.length : -1')}"
            ) from exc

    def sleep(self, seconds):
        # Playwright (sync) only dispatches route/request handlers while the test thread is inside
        # a Playwright call, so never use time.sleep() when observing browser-side requests.
        self.page.wait_for_timeout(int(seconds * 1000))

    def wait(self, expr, timeout=8000):
        self.page.wait_for_function(expr, timeout=timeout)

    def state(self):
        return self.js("document.getElementById('voiceOrb').dataset.state")

    def status(self):
        return self.js("document.getElementById('voiceStatus').textContent")

    def start(self):
        self.page.click("#voiceMode")

    def listening(self):
        self.wait("window.__rec.live().length>0")
        self.wait("document.getElementById('voiceOrb').dataset.state==='listening'")

    def say(self, text):
        assert self.js(f"window.__rec.emit({text!r}, true)")

    def turns(self, n, timeout=10000):
        deadline = time.time() + timeout / 1000
        while time.time() < deadline and len(self.provider.requests) < n:
            self.sleep(0.05)
        assert len(self.provider.requests) >= n


@pytest.fixture
def ctx(server, browser):
    base, provider = server
    provider.requests.clear()
    provider.reply = "Noted."
    provider.delay = 0.0
    context = browser.new_context()
    context.add_init_script(FAKES)
    page = context.new_page()
    c = Ctx(page, provider, base)
    page.on("request", lambda r: c.requests.append(r.url))
    page.goto(base + "/assistant")
    page.wait_for_selector("#voiceMode")
    yield c
    context.close()


def test_session_listens_submits_speaks_and_listens_again(ctx):
    ctx.provider.reply = "We chose PostgreSQL for billing."
    assert ctx.state() == "idle"
    ctx.start()
    ctx.listening()
    assert ctx.js("document.getElementById('voiceMicBadge').dataset.on") == "true"
    ctx.say("Which database did we choose for the billing service?")
    assert "Hearing" in ctx.js("document.getElementById('voicePreview').textContent")
    ctx.turns(1)
    first = ctx.provider.requests[0]
    assert first.messages[-1]["content"].startswith("Which database")
    assert any("PostgreSQL" in m["content"] and "<memory_data>" in m["content"] for m in first.messages)
    ctx.wait("window.__tts.spoken.length>=1")
    assert "PostgreSQL" in ctx.js("window.__tts.spoken[0].text")
    # listening resumes on a NEW recognition session without any click
    ctx.wait("window.__rec.instances.length>=2 && window.__rec.live().length===1")
    ctx.listening()
    ctx.say("And why did we pick it?")
    ctx.turns(2)
    second = ctx.provider.requests[1]
    texts = [m["content"] for m in second.messages]
    assert any("Which database" in t for t in texts), "voice turns must keep conversation context"
    assert any("We chose PostgreSQL for billing." in t for t in texts)
    assert not any("/api/voice/transcribe" in u for u in ctx.requests), "browser mode uploads no audio"


def test_no_duplicate_submission_when_speech_repeats(ctx):
    ctx.provider.delay = 0.6
    ctx.start()
    ctx.listening()
    ctx.say("first part")
    ctx.say("second part")
    ctx.turns(1)
    ctx.sleep(0.2)
    # speech heard while Mia is thinking must be ignored (recognizer is stopped)
    assert ctx.js("window.__rec.live().length") == 0
    assert ctx.js("window.__rec.emit('late echo', true)") is False
    ctx.sleep(2.0)
    assert len(ctx.provider.requests) == 1
    assert ctx.provider.user_prompts()[0] == "first part second part"


def test_interrupt_button_stops_speech_and_returns_to_listening(ctx):
    ctx.provider.reply = "A long answer. " * 3
    ctx.js("window.__tts.duration=10000")
    ctx.start()
    ctx.listening()
    ctx.say("tell me something")
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='speaking'")
    assert ctx.js("document.getElementById('voiceInterrupt').hidden") is False
    ctx.page.click("#voiceInterrupt")
    ctx.wait("window.__tts.cancelled>=1")
    ctx.wait("window.__rec.live().length===1")
    ctx.listening()
    spoken_before = ctx.js("window.__tts.spoken.length")
    ctx.sleep(0.5)
    assert ctx.js("window.__tts.spoken.length") == spoken_before, "no further chunks after interrupt"
    assert ctx.js("window.__tts.overlap") == 0
    assert ctx.js("document.getElementById('voiceInterrupt').hidden") is True


def test_escape_key_interrupts_speech(ctx):
    ctx.provider.reply = "One sentence."
    ctx.js("window.__tts.duration=10000")
    ctx.start()
    ctx.listening()
    ctx.say("hello")
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='speaking'")
    ctx.page.keyboard.press("Escape")
    ctx.wait("window.__tts.cancelled>=1")
    ctx.listening()


@pytest.mark.parametrize("phrase", ["goodbye Mia", "Stop listening.", "okay Mia, please stop listening"])
def test_end_phrases_stop_session_without_ai_call_or_restart(ctx, phrase):
    ctx.start()
    ctx.listening()
    ctx.say(phrase)
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='stopped'")
    count = ctx.js("window.__rec.instances.length")
    ctx.sleep(1.8)
    assert ctx.js("window.__rec.live().length") == 0
    assert ctx.js("window.__rec.instances.length") == count, "stopped session must not restart"
    assert ctx.provider.requests == [], "stop phrases are commands, not model prompts"
    assert ctx.js("document.getElementById('voiceMicBadge').dataset.on") == "false"


def test_stop_speaking_phrase_keeps_session_alive(ctx):
    ctx.start()
    ctx.listening()
    ctx.say("stop speaking")
    ctx.wait("window.__rec.instances.length>=2 && window.__rec.live().length===1")
    assert ctx.provider.requests == []
    assert ctx.state() in {"ready", "listening"}


def test_long_question_containing_stop_words_is_a_normal_prompt(ctx):
    ctx.start()
    ctx.listening()
    ctx.say("how do I stop listening to my neighbours music when they play it very loudly")
    ctx.turns(1)


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("Sunna band karo", "end"),
        ("mia sunna band kar do", "end"),
        ("Khuda hafiz Mia", "end"),
        ("اللہ حافظ میا", "end"),
        ("سننا بند کرو", "end"),
        ("Chup ho jao", "stopSpeaking"),
        ("chup karo yaar", "stopSpeaking"),
        ("Bas karo.", "stopSpeaking"),
        ("بس کرو", "stopSpeaking"),
        ("چپ ہو جاؤ میا", "stopSpeaking"),
        ("بولنا بند کرو", "stopSpeaking"),
        ("Goodbye Mia", "end"),
        ("be quiet", "stopSpeaking"),
    ],
)
def test_multilingual_voice_commands_are_classified(ctx, phrase, expected):
    assert ctx.js(f"classifyVoiceCommand({json.dumps(phrase)})") == expected


@pytest.mark.parametrize(
    "phrase",
    [
        "mujhe batao ke bacchon ko bas karo kehne ka sahi tareeqa kya hai",
        "mujhe sunna band karo wali app ki setting kahan milti hai",
        "مجھے بتاؤ کہ بچوں کو بس کرو کہنے کا صحیح طریقہ کیا ہے",
        "پڑوسی کی چپ کرو والی آواز کیسے بند کروں جب وہ بہت زور سے گانا چلائیں",
        "chup ho jao ka matlab kya hota hai English mein batao please",
        "hello mia",
        "",
    ],
)
def test_long_or_unrelated_urdu_text_is_not_a_voice_command(ctx, phrase):
    assert ctx.js(f"classifyVoiceCommand({json.dumps(phrase)})") is None


@pytest.mark.parametrize("phrase", ["Sunna band karo", "سننا بند کرو"])
def test_urdu_end_phrases_stop_the_session_without_ai_call(ctx, phrase):
    ctx.start()
    ctx.listening()
    ctx.say(phrase)
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='stopped'")
    ctx.sleep(1.2)
    assert ctx.js("window.__rec.live().length") == 0
    assert ctx.provider.requests == []
    assert ctx.js("document.getElementById('voiceMicBadge').dataset.on") == "false"


@pytest.mark.parametrize("phrase", ["Chup ho jao", "بس کرو"])
def test_urdu_stop_speaking_phrases_keep_the_session_alive(ctx, phrase):
    ctx.start()
    ctx.listening()
    ctx.say(phrase)
    ctx.wait("window.__rec.instances.length>=2 && window.__rec.live().length===1")
    assert ctx.provider.requests == []
    assert ctx.state() in {"ready", "listening"}


def test_end_conversation_button_cancels_speech_and_never_restarts(ctx):
    ctx.provider.reply = "Speaking for a while."
    ctx.js("window.__tts.duration=10000")
    ctx.start()
    ctx.listening()
    ctx.say("go")
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='speaking'")
    ctx.page.click("#voiceMode")
    ctx.wait("window.__tts.cancelled>=1")
    count = ctx.js("window.__rec.instances.length")
    ctx.sleep(1.8)
    assert ctx.state() == "stopped"
    assert ctx.js("window.__rec.live().length") == 0
    assert ctx.js("window.__rec.instances.length") == count
    assert ctx.js("window.__tts.current") is None


def test_microphone_permission_denied_is_reported_and_not_retried(ctx):
    ctx.start()
    ctx.wait("window.__rec.live().length>0")
    ctx.js("window.__rec.error('not-allowed')")
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='error'")
    assert "permission was denied" in ctx.status()
    count = ctx.js("window.__rec.instances.length")
    ctx.sleep(1.5)
    assert ctx.js("window.__rec.instances.length") == count


def test_restart_loop_is_detected_and_stops(ctx):
    ctx.js("window.__rec.autoEnd=true")
    ctx.start()
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='error'", timeout=15000)
    assert "restart loop" in ctx.status()
    assert ctx.js("window.__rec.instances.length") <= 8


def test_mute_stops_microphone_and_unmute_resumes(ctx):
    ctx.start()
    ctx.listening()
    ctx.page.click("#voiceMute")
    assert ctx.state() == "muted"
    assert ctx.js("window.__rec.live().length") == 0
    assert ctx.js("window.__rec.emit('should be ignored', true)") is False
    ctx.sleep(1.2)
    assert ctx.js("window.__rec.live().length") == 0, "muted mic must not restart"
    ctx.page.click("#voiceMute")
    ctx.wait("window.__rec.live().length===1")


def test_push_to_talk_records_only_while_held(ctx):
    ctx.page.select_option("#voiceInputMode", "ptt")
    ctx.start()
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='ready'")
    ctx.sleep(0.4)
    assert ctx.js("window.__rec.instances.length") == 0, "mic stays off until the button is held"
    ctx.page.locator("#voicePtt").hover()
    ctx.page.mouse.down()
    ctx.wait("window.__rec.live().length===1")
    ctx.say("push to talk message")
    ctx.sleep(1.8)  # longer than the hands-free silence timer: must not auto-submit
    assert ctx.provider.requests == []
    ctx.page.mouse.up()
    ctx.turns(1)
    assert ctx.provider.user_prompts()[0] == "push to talk message"
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='ready'")
    ctx.sleep(1.0)
    assert ctx.js("window.__rec.live().length") == 0, "push-to-talk never re-opens the mic by itself"


def test_push_to_talk_space_key(ctx):
    ctx.page.select_option("#voiceInputMode", "ptt")
    ctx.start()
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='ready'")
    ctx.page.keyboard.down("Space")
    ctx.wait("window.__rec.live().length===1")
    ctx.say("space bar message")
    ctx.page.keyboard.up("Space")
    ctx.turns(1)
    assert ctx.provider.user_prompts()[0] == "space bar message"


def test_recognition_language_is_configurable_english_and_urdu(ctx):
    ctx.start()
    ctx.listening()
    assert ctx.js("window.__rec.live()[0].lang") == "en-US"
    assert ctx.js("document.getElementById('voiceLanguage').disabled") is True
    ctx.page.click("#voiceMode")
    ctx.page.select_option("#voiceLanguage", "ur-PK")
    ctx.start()
    ctx.wait("window.__rec.live().length===1")
    assert ctx.js("window.__rec.live()[0].lang") == "ur-PK"
    ctx.say("میرا نام نعمان ہے")
    ctx.turns(1)
    assert ctx.provider.user_prompts()[0] == "میرا نام نعمان ہے"


def test_voice_interrupt_by_barge_in_when_enabled(ctx):
    ctx.provider.reply = "Here is a long explanation. " * 4
    ctx.js("window.__tts.duration=10000")
    ctx.page.check("#voiceBargeIn")
    ctx.start()
    ctx.listening()
    ctx.say("explain")
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='speaking'")
    ctx.wait("window.__rec.live().length===1")
    ctx.js("window.__tts.duration=100")
    ctx.say("wait actually tell me about billing")
    ctx.wait("window.__tts.cancelled>=1")
    ctx.say("wait actually tell me about billing")
    ctx.turns(2)
    assert ctx.provider.user_prompts()[1] == "wait actually tell me about billing"


def test_barge_in_is_off_by_default(ctx):
    assert ctx.js("document.getElementById('voiceBargeIn').checked") is False


def test_long_reply_is_spoken_in_sequential_chunks_with_speed_and_volume(ctx):
    ctx.provider.reply = " ".join(f"This is sentence number {i} of a rather long answer." for i in range(30))
    ctx.page.fill("#voiceRate", "1.3")
    ctx.page.fill("#voiceVolume", "0.5")
    ctx.start()
    ctx.listening()
    ctx.say("give me the long version")
    ctx.wait("window.__tts.spoken.length>=4", timeout=15000)
    spoken = ctx.js("window.__tts.spoken")
    assert all(len(s["text"]) <= 220 for s in spoken)
    assert all(abs(s["rate"] - 1.3) < 1e-6 and abs(s["volume"] - 0.5) < 1e-6 for s in spoken)
    assert spoken[0]["text"].startswith("This is sentence number 0")
    assert ctx.js("window.__tts.overlap") == 0


def test_local_tts_failure_falls_back_to_browser_speech(ctx):
    ctx.provider.reply = "Fallback voice test."
    ctx.js(
        "(()=>{const o=new Option('Broken local','local:does-not-exist');"
        "document.getElementById('voiceChoice').add(o);document.getElementById('voiceChoice').value=o.value;})()"
    )
    ctx.start()
    ctx.listening()
    ctx.say("speak please")
    ctx.wait("window.__tts.spoken.length>=1", timeout=12000)
    assert "Fallback voice test." in ctx.js("window.__tts.spoken[0].text")


def test_voice_preview_speaks_and_reports(ctx):
    ctx.page.click("#previewVoice")
    ctx.wait("window.__tts.spoken.length>=1")
    ctx.wait("document.getElementById('voiceStatus').textContent.includes('preview finished')")
    assert ctx.state() == "idle"
    assert ctx.provider.requests == []


def test_unsupported_browser_reports_clearly_and_never_opens_mic(server, browser):
    base, _ = server
    context = browser.new_context()
    context.add_init_script(FAKES)
    page = context.new_page()
    page.goto(base + "/assistant?norec=1")
    page.click("#voiceMode")
    page.wait_for_function("document.getElementById('voiceOrb').dataset.state==='error'")
    assert "not supported" in page.inner_text("#voiceStatus")
    assert page.evaluate("window.__rec.instances.length") == 0
    context.close()


def test_provider_failure_is_reported_and_listening_resumes(ctx):
    ctx.page.route(
        "**/api/gateway/chat",
        lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"No provider"}'),
    )
    ctx.start()
    ctx.listening()
    ctx.say("hello there")
    ctx.wait("document.getElementById('messages').textContent.includes('could not complete')")
    ctx.wait("window.__rec.instances.length>=2 && window.__rec.live().length===1")
    assert ctx.state() in {"ready", "listening"}


def test_going_offline_stops_the_browser_session(ctx):
    ctx.start()
    ctx.listening()
    ctx.page.context.set_offline(True)
    ctx.wait("document.getElementById('voiceOrb').dataset.state==='error'")
    assert "Network connection lost" in ctx.status()
    assert ctx.js("window.__rec.live().length") == 0


def test_voice_does_not_auto_approve_sensitive_actions(ctx):
    approvals: list[str] = []
    ctx.page.on("request", lambda r: approvals.append(r.url) if "approv" in r.url else None)
    body = (
        '{"content":"I need your approval to write a file.","conversation_id":"c1","provider_id":"p",'
        '"model_id":"m","context_references":[],"pending_actions":[{"id":"a1","kind":"file.write",'
        '"summary":"Write notes.txt","display":{"target":"notes.txt"}}]}'
    )
    ctx.page.route("**/api/gateway/chat", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
    ctx.start()
    ctx.listening()
    ctx.say("please write a file called notes dot txt")
    ctx.wait("document.querySelectorAll('.approval-card').length===1")
    ctx.sleep(1.5)
    assert approvals == [], "voice must never approve actions; the owner has to click the card"


def test_privacy_disclosure_and_mic_badge(ctx):
    text = ctx.page.inner_text(".voice-privacy")
    assert "may process microphone audio outside Mia" in text
    assert ctx.js("document.getElementById('voiceMicBadge').textContent") == "Mic off"
    ctx.start()
    ctx.listening()
    assert "browser speech service" in ctx.js("document.getElementById('voiceMicBadge').textContent")
    ctx.page.click("#voiceMode")
    assert ctx.js("document.getElementById('voiceMicBadge').textContent") == "Mic off"


# ---------------------------------------------------------------------------------------------
# Local (offline) engine: Chromium's fake audio device plays a generated WAV; the transcription
# endpoint is mocked because Faster-Whisper model weights cannot be downloaded in CI.
# ---------------------------------------------------------------------------------------------

import math  # noqa: E402
import struct  # noqa: E402
import wave  # noqa: E402

GUM_LOG = r"""
(() => {
  window.__gum = {calls: [], tracks: []};
  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => {
    window.__gum.calls.push(c);
    // WebRTC noise suppression would remove the synthetic steady tone used as fake speech.
    const relaxed = {audio: Object.assign({}, c.audio,
      {echoCancellation: false, noiseSuppression: false, autoGainControl: false})};
    const stream = await original(relaxed);
    stream.getTracks().forEach(t => window.__gum.tracks.push(t));
    return stream;
  };
})();
"""


def write_wav(path, pattern, rate=16000):
    frames = bytearray()
    for seconds, amplitude in pattern:
        for i in range(int(seconds * rate)):
            value = int(32767 * amplitude * math.sin(2 * math.pi * 440 * i / rate))
            frames += struct.pack("<h", value)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Uploads:
    def __init__(self):
        self.items: list[dict] = []
        self.text = "hello from the local engine"
        self.status = 200

    def handler(self, route, request):
        self.items.append(
            {
                "url": request.url,
                "type": request.headers.get("content-type"),
                "body": request.post_data_buffer,
            }
        )
        if self.status == 200:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"text": self.text}))
        else:
            route.fulfill(
                status=self.status,
                content_type="application/json",
                body=json.dumps({"detail": "Install the voice extra."}),
            )


@pytest.fixture
def local_mic(server, pw, tmp_path):
    base, provider = server
    provider.requests.clear()
    provider.reply = "Noted."
    provider.delay = 0.0
    made = []

    def make(pattern, *, status_ready=True):
        wav = tmp_path / f"mic{len(made)}.wav"
        write_wav(wav, pattern)
        try:
            browser = pw.chromium.launch(
                args=[
                    "--use-fake-device-for-media-stream",
                    "--use-fake-ui-for-media-stream",
                    f"--use-file-for-fake-audio-capture={wav}",
                ]
            )
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"Chromium unavailable: {exc}")
        context = browser.new_context(permissions=["microphone"])
        context.add_init_script(FAKES)
        context.add_init_script(GUM_LOG)
        page = context.new_page()
        uploads = Uploads()
        page.route("**/api/voice/transcribe*", uploads.handler)
        if status_ready:
            page.route(
                "**/api/voice/status",
                lambda route: route.fulfill(
                    status=200, content_type="application/json", body='{"speech_to_text":"ready"}'
                ),
            )
        page.goto(base + "/assistant")
        page.wait_for_selector("#voiceMode")
        page.select_option("#voiceEngine", "local")
        c = Ctx(page, provider, base)
        made.append(browser)
        return c, uploads

    yield make
    for browser in made:
        browser.close()


def wait_uploads(c, uploads, n, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline and len(uploads.items) < n:
        c.sleep(0.05)
    assert len(uploads.items) >= n, "expected the local engine to upload an utterance"


SPEECH_LOOP = [(1.0, 0.0), (1.5, 0.5), (3.0, 0.0)]


def test_local_engine_detects_utterance_sends_wav_and_uses_normal_pipeline(local_mic):
    c, uploads = local_mic(SPEECH_LOOP)
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='listening'", timeout=10000)
    assert "local Faster-Whisper" in c.js("document.getElementById('voiceMicBadge').textContent")
    wait_uploads(c, uploads, 1)
    item = uploads.items[0]
    assert item["type"] == "audio/wav"
    assert "language=en" in item["url"] and "filename=utterance.wav" in item["url"]
    body = item["body"]
    assert body[:4] == b"RIFF" and body[8:12] == b"WAVE"
    with wave.open(__import__("io").BytesIO(body)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert 0.5 < w.getnframes() / 16000 < 4.0
    c.turns(1)
    assert c.provider.user_prompts()[0] == "hello from the local engine"
    # the engine used only Mia's own API for audio
    assert all(u.startswith(c.base) or u.startswith("blob:") or u.startswith("data:") for u in c.requests)


def test_local_engine_releases_microphone_when_session_ends(local_mic):
    c, uploads = local_mic(SPEECH_LOOP)
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='listening'", timeout=10000)
    assert c.js("window.__gum.tracks.some(t=>t.readyState==='live')") is True
    c.page.click("#voiceMode")
    c.wait("window.__gum.tracks.every(t=>t.readyState==='ended')")
    count = len(uploads.items)
    c.sleep(3.5)
    assert len(uploads.items) == count, "no audio may be uploaded after the session ended"
    assert c.state() == "stopped"


def test_local_engine_ignores_silence(local_mic):
    c, uploads = local_mic([(6.0, 0.0)])
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='listening'", timeout=10000)
    c.sleep(3.5)
    assert uploads.items == []
    assert c.provider.requests == []


def test_local_engine_reports_missing_install_without_opening_mic(local_mic):
    c, _ = local_mic(SPEECH_LOOP, status_ready=False)
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='error'")
    assert 'pip install -e ".[voice]"' in c.status()
    assert c.js("window.__gum.calls.length") == 0


def test_local_engine_transcription_failure_stops_session_and_releases_mic(local_mic):
    c, uploads = local_mic(SPEECH_LOOP)
    uploads.status = 503
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='error'", timeout=15000)
    assert "Install the voice extra." in c.status()
    c.wait("window.__gum.tracks.every(t=>t.readyState==='ended')")


def test_local_engine_language_parameter_for_urdu_and_auto(local_mic):
    c, uploads = local_mic(SPEECH_LOOP)
    c.page.select_option("#voiceLanguage", "ur-PK")
    c.start()
    wait_uploads(c, uploads, 1)
    assert "language=ur" in uploads.items[0]["url"]
    c.page.close()
    c2, uploads2 = local_mic(SPEECH_LOOP)
    c2.page.select_option("#voiceLanguage", "auto")
    c2.start()
    wait_uploads(c2, uploads2, 1)
    assert "language=" not in uploads2.items[0]["url"], "auto lets Whisper detect English/Urdu per utterance"


def test_microphone_device_selection_reaches_the_local_engine(local_mic):
    c, _ = local_mic(SPEECH_LOOP)
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='listening'", timeout=10000)
    c.page.click("#voiceMode")
    c.wait("document.getElementById('micDevice').options.length>=2")
    device_id = c.js("document.getElementById('micDevice').options[1].value")
    c.page.select_option("#micDevice", device_id)
    c.start()
    c.wait("window.__gum.calls.length>=2", timeout=10000)
    last = c.js("window.__gum.calls[window.__gum.calls.length-1]")
    assert last["audio"]["deviceId"]["exact"] == device_id


def test_local_engine_push_to_talk_transcribes_on_release(local_mic):
    c, uploads = local_mic([(4.0, 0.5)])
    c.page.select_option("#voiceInputMode", "ptt")
    c.start()
    c.wait("document.getElementById('voiceOrb').dataset.state==='ready'")
    assert c.js("window.__gum.calls.length") == 0, "mic stays closed until the button is held"
    c.page.locator("#voicePtt").hover()
    c.page.mouse.down()
    c.wait_state("listening")
    c.sleep(1.2)
    c.page.mouse.up()
    wait_uploads(c, uploads, 1)
    c.turns(1)
    assert c.provider.user_prompts()[0] == "hello from the local engine"
    c.wait("window.__gum.tracks.every(t=>t.readyState==='ended')")


def test_wake_phrase_ignores_unaddressed_speech_then_submits_after_hey_mia(ctx):
    ctx.page.click("#wakeWordMode")
    ctx.listening()
    assert "Listening for 'Hey Mia'" in ctx.status()
    ctx.say("Hello Mia, I am speaking to someone else")
    ctx.sleep(0.1)
    assert ctx.provider.requests == []

    ctx.say("Hey Mia, which database did we choose for billing?")
    ctx.turns(1)
    assert "which database did we choose for billing" in ctx.provider.user_prompts()[0].lower()
    assert "hey mia" not in ctx.provider.user_prompts()[0].lower()
    ctx.wait("window.__tts.spoken.length>=1")
    ctx.wait("window.__rec.live().length>0")
    assert "Listening for 'Hey Mia'" in ctx.status()

    ctx.page.click("#wakeWordMode")
    ctx.wait("window.__rec.live().length===0")
    assert ctx.js("document.getElementById('voiceMicBadge').dataset.on") == "false"
    assert ctx.state() == "stopped"
