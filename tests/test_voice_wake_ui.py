"""Browser tests for the opt-in offline wake-word panel (MIA-UX-04).

Real Chromium with its fake microphone device -> real AudioWorklet -> real WebSocket -> real uvicorn
server -> fake detector. No real speech or real microphone is involved, so these prove wiring, privacy
controls and lifecycle, not detection quality.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

import mia.voice.wake_transport as wake_transport
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")

INSTRUMENT = r"""
(() => {
  const w = window.__wake = {sockets: [], gum: 0, streams: []};
  const NativeWS = window.WebSocket;
  window.WebSocket = function (...args) {
    const ws = new NativeWS(...args);
    w.sockets.push(ws);
    return ws;
  };
  window.WebSocket.prototype = NativeWS.prototype;
  Object.assign(window.WebSocket, {CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3});
  const nativeGum = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (...args) => {
    w.gum += 1;
    const stream = await nativeGum(...args);
    w.streams.push(stream);
    return stream;
  };
  w.liveTracks = () => w.streams.flatMap(s => s.getTracks()).filter(t => t.readyState === 'live').length;
  class FakeRecognition {
    constructor() { this.active = false; }
    start() { this.active = true; setTimeout(() => this.onstart && this.onstart(), 5); }
    stop() { this.active = false; setTimeout(() => this.onend && this.onend(), 5); }
    abort() { this.active = false; }
  }
  window.SpeechRecognition = FakeRecognition;
  window.webkitSpeechRecognition = FakeRecognition;
})();
"""


class FakeDetector:
    name = "fake"

    def __init__(self):
        self.scored = 0
        self.closed = 0
        self.fire_after = None  # frames before the score jumps above threshold

    def score(self, frame):
        self.scored += 1
        if self.fire_after is not None and self.scored > self.fire_after:
            return 0.99
        return 0.0

    def reset(self):
        pass

    def close(self):
        self.closed += 1


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _start(app):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture()
def operational(tmp_path, monkeypatch):
    """A server whose offline wake word reports operational (fake detector)."""
    model = tmp_path / "model.onnx"
    model.write_bytes(b"placeholder")
    monkeypatch.setenv("MIA_WAKE_MODEL_PATH", str(model))
    real = wake_transport.importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name in {"openwakeword", "onnxruntime"}:
            return object()
        return real(name, *args, **kwargs)

    monkeypatch.setattr(wake_transport.importlib.util, "find_spec", find_spec)
    detector = FakeDetector()
    monkeypatch.setattr(wake_transport, "build_default_detector", lambda: detector)
    app = create_app(store=ObservatoryStore(tmp_path / "ui.sqlite3"))
    server, thread, base = _start(app)
    yield base, detector
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def unavailable(tmp_path, monkeypatch):
    monkeypatch.delenv("MIA_WAKE_MODEL_PATH", raising=False)
    real = wake_transport.importlib.util.find_spec

    def find_spec(name, *args, **kwargs):
        if name in {"openwakeword", "onnxruntime"}:
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(wake_transport.importlib.util, "find_spec", find_spec)
    app = create_app(store=ObservatoryStore(tmp_path / "ui-off.sqlite3"))
    server, thread, base = _start(app)
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(
                args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


@pytest.fixture()
def open_page(browser):
    contexts = []

    def opener(base):
        context = browser.new_context(permissions=["microphone"])
        contexts.append(context)
        context.add_init_script(INSTRUMENT)
        page = context.new_page()
        page.goto(base + "/assistant")
        page.wait_for_selector("#offlineWake")
        return page

    yield opener
    for context in contexts:
        context.close()


def wait_until(predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def badge(page):
    return page.inner_text("#offlineWakeBadge")


def note(page):
    return page.inner_text("#offlineWakeNote")


# ------------------------------------------------------------------ availability


def test_panel_is_honest_when_the_offline_wake_word_is_not_available(unavailable, open_page):
    page = open_page(unavailable)
    page.wait_for_function("document.getElementById('offlineWakeNote').textContent.includes('voice-wake')")
    assert page.is_disabled("#offlineWakeStart")
    assert badge(page) == "Not listening"
    assert page.evaluate("window.__wake.gum") == 0
    assert page.evaluate("window.__wake.sockets.length") == 0


def test_page_load_never_opens_the_microphone_or_a_socket(operational, open_page):
    base, detector = operational
    page = open_page(base)
    page.wait_for_function("!document.getElementById('offlineWakeStart').disabled")
    assert "press the button" in note(page).lower()
    page.wait_for_timeout(500)
    assert page.evaluate("window.__wake.gum") == 0
    assert page.evaluate("window.__wake.sockets.length") == 0
    assert detector.scored == 0
    assert badge(page) == "Not listening"


# --------------------------------------------------------------- lifecycle & privacy


def start_listening(page):
    page.wait_for_function("!document.getElementById('offlineWakeStart').disabled")
    page.click("#offlineWakeStart")
    page.wait_for_function("document.getElementById('offlineWakeBadge').dataset.on==='true'")


def test_start_shows_a_persistent_badge_and_streams_audio_to_the_local_detector(operational, open_page):
    base, detector = operational
    page = open_page(base)
    start_listening(page)
    assert "Listening for" in badge(page) and "offline" in badge(page)
    assert page.is_visible("#offlineWakeStop")
    assert page.is_hidden("#offlineWakeStart")
    assert wait_until(lambda: detector.scored >= 3), "microphone frames must reach the detector"
    assert page.evaluate("window.__wake.liveTracks()") == 1
    # Only loopback transport is used.
    assert page.evaluate("window.__wake.sockets[0].url").startswith("ws://127.0.0.1:")


def test_stop_button_stops_immediately_and_releases_the_microphone(operational, open_page):
    base, detector = operational
    page = open_page(base)
    start_listening(page)
    assert wait_until(lambda: detector.scored >= 1)
    page.click("#offlineWakeStop")
    page.wait_for_function("document.getElementById('offlineWakeBadge').dataset.on==='false'")
    assert page.evaluate("window.__wake.liveTracks()") == 0
    assert page.evaluate("window.__wake.sockets[0].readyState") in (2, 3)
    assert wait_until(lambda: detector.closed == 1), "server must release the detector"
    # The page told the server explicitly (not just by dropping the socket).
    status = page.request.get(base + "/api/voice/wake/status").json()["session"]
    assert status["stop_reason"] == "owner" and status["listening"] is False
    settled = detector.scored
    page.wait_for_timeout(400)
    assert detector.scored == settled, "no audio may be scored after stop"
    assert page.is_visible("#offlineWakeStart") and page.is_hidden("#offlineWakeStop")


def test_stop_from_the_server_is_reflected_in_the_page_and_releases_the_microphone(operational, open_page):
    base, detector = operational
    page = open_page(base)
    start_listening(page)
    assert wait_until(lambda: detector.scored >= 1)
    page.request.post(base + "/api/voice/wake/stop")
    page.wait_for_function("document.getElementById('offlineWakeBadge').dataset.on==='false'")
    assert page.evaluate("window.__wake.liveTracks()") == 0
    # The server's own `stopped` message is shown (not the generic socket-closed fallback).
    assert wait_until(lambda: note(page) == "Stopped listening for the wake word.")


def test_wake_event_stops_the_detector_and_hands_off_to_a_voice_conversation(operational, open_page):
    base, detector = operational
    detector.fire_after = 6
    page = open_page(base)
    start_listening(page)
    page.wait_for_function("document.getElementById('voiceMode').textContent==='End conversation'")
    assert badge(page) == "Not listening"
    assert page.evaluate("window.__wake.liveTracks()") == 0, "wake microphone is released at hand-off"
    assert wait_until(lambda: detector.closed == 1)
    assert page.evaluate("document.getElementById('voiceOrb').dataset.state") != "error"


def test_starting_a_voice_conversation_turns_the_offline_wake_word_off(operational, open_page):
    base, detector = operational
    page = open_page(base)
    start_listening(page)
    page.click("#voiceMode")
    page.wait_for_function("document.getElementById('offlineWakeBadge').dataset.on==='false'")
    assert wait_until(lambda: detector.closed == 1)
    assert page.evaluate("window.__wake.liveTracks()") == 0


def test_cannot_start_while_a_voice_conversation_is_active(operational, open_page):
    base, detector = operational
    page = open_page(base)
    page.wait_for_function("!document.getElementById('offlineWakeStart').disabled")
    page.click("#voiceMode")
    page.wait_for_function("document.getElementById('voiceMode').textContent==='End conversation'")
    page.click("#offlineWakeStart")
    assert "end the current voice session" in note(page).lower()
    assert page.evaluate("window.__wake.sockets.length") == 0
    assert detector.scored == 0


def test_leaving_the_page_releases_everything(operational, open_page):
    base, detector = operational
    page = open_page(base)
    start_listening(page)
    assert wait_until(lambda: detector.scored >= 1)
    page.goto("about:blank")
    assert wait_until(lambda: detector.closed == 1), "closing the page must stop listening server-side"


# --------------------------------------------------------------------- resampler


def test_resampler_produces_16k_mono_int16_and_carries_state_across_blocks(operational, open_page):
    base, _ = operational
    page = open_page(base)
    result = page.evaluate(
        """() => {
          const out = {};
          const run = (rate, blocks, value) => {
            const r = createWakeResampler(rate);
            let total = 0, last = null;
            for (let i = 0; i < blocks; i++) {
              const o = r(new Float32Array(128).fill(value)); total += o.length; if (o.length) last = o[o.length-1];
            }
            return {total, last};
          };
          out.r48 = run(48000, 30, 0.5);
          out.r44 = run(44100, 30, 0.5);
          out.r16 = run(16000, 10, 0.25);
          out.clip = Array.from(createWakeResampler(16000)(Float32Array.from([2, -2, 0])));
          return out;
        }"""
    )
    assert abs(result["r48"]["total"] - 30 * 128 / 3) <= 2 and result["r48"]["last"] == 16384
    assert abs(result["r44"]["total"] - 30 * 128 * 16000 / 44100) <= 2 and result["r44"]["last"] == 16384
    assert result["r16"]["total"] == 10 * 128 and result["r16"]["last"] == 8192
    assert result["clip"][:2] == [32767, -32767] or result["clip"][:2] == [32767, -32768]
