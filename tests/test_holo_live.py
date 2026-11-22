"""MIA-HOLO-04: the hologram shows what Mia is really doing.

Layers:
* a Node harness runs the *real* ``streamReply`` from assistant.html with a recording reporter;
* Chromium tests run the real server, the real /assistant page and the real /hologram page in one
  browser context, so evidence travels over a real BroadcastChannel and the real
  ``POST /api/runtime/resolve``.

What is faked, said plainly: the model reply (a controllable fetch inside the assistant page) and the
voice/wake *state changes* (the page's own state functions are called; no microphone or speech
engine runs). So these prove the wiring, privacy, staleness, offline and reconnect behaviour, not
speech or model quality. Software GL only; no real-GPU evidence (MIA-HOLO-06).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from mia.computer.approvals import ApprovalStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore

playwright_sync = pytest.importorskip("playwright.sync_api")

ASSISTANT = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"
ALLOWED_KEYS = {"v", "sender", "type", "kind", "detail"}
FAST = "?poll=300"  # poll every 300 ms instead of 2 s so tests stay quick


# ============================================================ Node harness: the real streamReply


def stream_reply_source() -> str:
    text = ASSISTANT.read_text(encoding="utf-8")
    start = text.index("async function streamReply(")
    return text[start : text.index("\nlet mediaRecorder=null;", start)]


HARNESS = r"""
const assert = require('assert');
__SOURCE__
const enc = new TextEncoder();
const sse = (name, payload) => enc.encode(`event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`);
function fakeFetch({chunks, hang, ok = true, status = 200}) {
  return async (url, opts) => {
    if (opts.signal && opts.signal.aborted) throw new DOMException('Aborted', 'AbortError');
    if (!ok) return {ok: false, status, json: async () => ({detail: 'provider unavailable'})};
    let i = 0;
    return {ok: true, body: {getReader() { return {
      read() {
        if (i < chunks.length) return Promise.resolve({value: chunks[i++], done: false});
        if (!hang) return Promise.resolve({value: undefined, done: true});
        return new Promise((_, reject) => {
          const fail = () => reject(new DOMException('Aborted', 'AbortError'));
          if (opts.signal.aborted) fail(); else opts.signal.addEventListener('abort', fail, {once: true});
        });
      }
    }; }}};
  };
}
const bubble = () => ({textContent: '', appendChild() {}, scrollIntoView() {}});
global.document = {createElement: () => ({style: {}, textContent: ''})};
const calls = [];
global.rt = {set(kind, detail) { calls.push(detail ? [kind, detail] : [kind]); }};
const run = async (fn) => { calls.length = 0; let thrown = null; let result = null;
  try { result = await fn(); } catch (e) { thrown = String(e.message); }
  return {calls: calls.map(c => c.slice()), thrown, result}; };

(async () => {
  const out = {};
  // 1. normal turn, one delta report per 2 s window
  const base = 1700000000000; const times = [base, base + 500, base + 2100];
  const realNow = Date.now; Date.now = () => times.shift();
  global.fetch = fakeFetch({chunks: [sse('delta', {text: 'a'}), sse('delta', {text: 'b'}), sse('delta', {text: 'c'}),
                                      sse('done', {conversation_id: 'c1'})]});
  const seen = [];
  out.normal = await run(() => streamReply({prompt: 'SECRET'}, bubble(), new AbortController().signal, t => seen.push(t)));
  Date.now = realNow;
  out.normal.forwarded = seen;
  // 2. owner stops mid-stream
  global.fetch = fakeFetch({chunks: [sse('delta', {text: 'Hel'})], hang: true});
  const c = new AbortController();
  out.stop = await run(async () => { const p = streamReply({prompt: 'x'}, bubble(), c.signal);
    await new Promise(r => setTimeout(r, 20)); c.abort(); return p; });
  // 3. stop before headers
  const c2 = new AbortController(); c2.abort(); global.fetch = fakeFetch({chunks: []});
  out.pre = await run(() => streamReply({prompt: 'x'}, bubble(), c2.signal));
  // 4. partial failure keeps text, reports partial
  global.fetch = fakeFetch({chunks: [sse('delta', {text: 'a'}), sse('error', {detail: 'cut', partial: true})]});
  out.partial = await run(() => streamReply({prompt: 'x'}, bubble(), new AbortController().signal));
  // 5. hard failures rethrow and report non-partial
  global.fetch = fakeFetch({chunks: [], ok: false, status: 503});
  out.http = await run(() => streamReply({prompt: 'x'}, bubble(), new AbortController().signal));
  global.fetch = fakeFetch({chunks: [sse('error', {detail: 'boom', partial: false})]});
  out.hard = await run(() => streamReply({prompt: 'x'}, bubble(), new AbortController().signal));
  // 6. stream that just ends without done
  global.fetch = fakeFetch({chunks: [sse('delta', {text: 'a'})]});
  out.truncated = await run(() => streamReply({prompt: 'x'}, bubble(), new AbortController().signal));
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
"""


@pytest.fixture(scope="module")
def harness_out(tmp_path_factory):
    if shutil.which("node") is None:
        pytest.skip("Node.js is not installed")
    script = tmp_path_factory.mktemp("rt") / "harness.js"
    script.write_text(HARNESS.replace("__SOURCE__", stream_reply_source()), encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    return json.loads(run.stdout.strip().splitlines()[-1])


def kinds(case):
    return [c[0] for c in case["calls"]]


def test_streamreply_reports_start_throttled_deltas_and_done(harness_out):
    case = harness_out["normal"]
    assert kinds(case) == ["chat_turn_started", "chat_delta", "chat_delta", "chat_done"]
    assert case["forwarded"] == ["a", "ab", "abc"]  # the speech streamer still gets every delta
    assert case["thrown"] is None


def test_streamreply_reports_owner_stop_mid_stream_and_before_headers(harness_out):
    assert kinds(harness_out["stop"]) == ["chat_turn_started", "chat_delta", "chat_stopped_by_owner"]
    assert kinds(harness_out["pre"]) == ["chat_turn_started", "chat_stopped_by_owner"]
    assert harness_out["stop"]["thrown"] is None


def test_streamreply_reports_failures_and_still_rethrows_them(harness_out):
    assert harness_out["partial"]["calls"][-1] == ["chat_error", {"partial": True}]
    assert harness_out["partial"]["thrown"] is None
    for name in ("http", "hard"):
        assert harness_out[name]["calls"][-1] == ["chat_error", {"partial": False}], name
        assert harness_out[name]["thrown"], name
    assert "chat_done" not in kinds(harness_out["http"])


def test_a_stream_that_ends_without_done_is_not_reported_as_finished(harness_out):
    # The page treats a stream that ends without "done" as an error, and the report says the same.
    assert kinds(harness_out["truncated"]) == ["chat_turn_started", "chat_delta", "chat_error"]
    assert "chat_done" not in kinds(harness_out["truncated"])
    assert harness_out["truncated"]["thrown"]


def test_no_report_carries_prompt_or_reply_text(harness_out):
    assert "SECRET" not in json.dumps(harness_out)


# ============================================================================== Chromium


def _start(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return server, thread, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    root = tmp_path_factory.mktemp("holo-live")
    approvals = ApprovalStore(root / "approvals.sqlite3")
    app = create_app(store=ObservatoryStore(root / "o.sqlite3"), approvals=approvals)
    server, thread, base = _start(app)
    yield base, approvals
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Chromium is not available for Playwright: {exc}")
        yield b
        b.close()


# A controllable model: the streaming and buffered chat endpoints are replaced inside the assistant
# page; every other request (lists, memory, runtime) goes to the real server.
FAKE_MODEL = r"""
(() => {
  const nativeFetch = window.fetch.bind(window);
  const enc = new TextEncoder();
  const ctl = window.__ctl = {stream: null, buffered: null};
  window.fetch = (url, opts = {}) => {
    const u = String(url);
    if (u.includes('/api/gateway/chat/stream')) {
      let c; const body = new ReadableStream({start(x) { c = x; }});
      ctl.stream = {send: (n, p) => c.enqueue(enc.encode(`event: ${n}\ndata: ${JSON.stringify(p)}\n\n`)),
                    close: () => c.close()};
      if (opts.signal) opts.signal.addEventListener('abort', () => {
        try { c.error(new DOMException('Aborted', 'AbortError')); } catch (e) {} });
      return Promise.resolve(new Response(body, {status: 200, headers: {'Content-Type': 'text/event-stream'}}));
    }
    if (u.endsWith('/api/gateway/chat')) {
      return new Promise((resolve, reject) => {
        ctl.buffered = {
          ok: (d) => resolve(new Response(JSON.stringify(d), {status: 200, headers: {'Content-Type': 'application/json'}})),
          fail: (status, detail) => resolve(new Response(JSON.stringify({detail}), {status, headers: {'Content-Type': 'application/json'}})),
        };
        if (opts.signal) opts.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
    }
    return nativeFetch(url, opts);
  };
})();
"""
DONE = {"conversation_id": "conv-holo-000001", "provider_id": "p", "model_id": "m", "input_tokens": 1,
        "output_tokens": 1, "cost_usd": 0, "context_references": []}
BUFFERED_OK = {"conversation_id": "conv-holo-000001", "content": "Hello.", "provider_id": "p", "model_id": "m",
               "context_references": [], "pending_actions": []}


@pytest.fixture()
def world(browser, served):
    base, approvals = served
    context = browser.new_context(viewport={"width": 1000, "height": 800})
    pages = []

    class World:
        def __init__(self):
            self.base, self.approvals, self.context = base, approvals, context
            self.errors = []

        def hologram(self, query=FAST):
            page = context.new_page()
            pages.append(page)
            page.on("pageerror", lambda e: self.errors.append(("hologram", str(e))))
            page.goto(f"{base}/hologram{query}")
            page.wait_for_function("window.__holo && window.__holoLive", timeout=15000)
            return page

        def assistant(self, *, tools=True, init=FAKE_MODEL):
            page = context.new_page()
            pages.append(page)
            page.on("pageerror", lambda e: self.errors.append(("assistant", str(e))))
            page.add_init_script(init)
            page.goto(f"{base}/assistant")
            page.wait_for_selector("#prompt")
            if not tools:
                page.uncheck("#useTools")
            return page

    yield World()
    for page in pages:
        try:
            page.close()
        except Exception:  # pragma: no cover
            pass
    context.close()
    for item in approvals.list_pending():  # leave the shared store clean for the next test
        approvals.reject(item["approval_id"])


def state(page):
    return page.evaluate("window.__holo.current()")


def wait_state(page, expected, timeout=10000):
    page.wait_for_function(f"window.__holo.current() === {json.dumps(expected)}", timeout=timeout)


def evidence_text(page):
    return page.locator("#evidence").inner_text()


def send(page, text="hello there"):
    page.fill("#prompt", text)
    page.press("#prompt", "Enter")


# ------------------------------------------------------------------ tracker (pure logic)


def test_tracker_holds_retracts_and_ages_evidence_per_sender(world):
    page = world.hologram()
    result = page.evaluate("""() => {
        let t = 0; const tr = window.__holoLive.createTracker(() => t);
        const set = (s, k, d) => tr.accept({v: 1, sender: s, type: 'set', kind: k, detail: d || {}});
        const out = {};
        set('a', 'voice_listening'); t = 4000;
        out.aged = tr.evidence();
        set('b', 'voice_listening'); t = 5000;
        out.newest_wins = tr.evidence().map(e => e.age_seconds);
        out.retract_a = tr.accept({v: 1, sender: 'a', type: 'retract', kind: 'voice_listening'});
        out.after_retract = tr.evidence().map(e => e.age_seconds);
        out.gone = tr.accept({v: 1, sender: 'b', type: 'gone'});
        out.after_gone = tr.evidence();
        t = 5000; set('c', 'chat_done'); t = 5000 + 601 * 1000;
        out.too_old = tr.evidence();
        return out;
    }""")
    assert result["aged"][0]["age_seconds"] == 4.0
    assert result["newest_wins"] == [1.0]  # one entry per kind, the freshest assertion
    assert result["retract_a"] is True and result["after_retract"] == [1.0]  # b's evidence untouched
    assert result["gone"] is True and result["after_gone"] == []
    assert result["too_old"] == []  # ancient evidence is dropped, not sent


def test_tracker_drops_malformed_messages_and_caps_memory(world):
    page = world.hologram()
    result = page.evaluate("""() => {
        const tr = window.__holoLive.createTracker(() => 0);
        const bad = [null, 'text', 5, {}, {v: 2, sender: 's', type: 'set', kind: 'chat_done'},
          {v: 1, sender: '', type: 'set', kind: 'chat_done'}, {v: 1, sender: 'x'.repeat(81), type: 'set', kind: 'chat_done'},
          {v: 1, sender: 's', type: 'set', kind: 'Bad Kind'}, {v: 1, sender: 's', type: 'set', kind: 5},
          {v: 1, sender: 's', type: 'explode', kind: 'chat_done'}, {v: 1, sender: 's', type: 'set'}];
        const accepted = bad.map(m => tr.accept(m));
        const detail = (tr.accept({v: 1, sender: 's', type: 'set', kind: 'chat_error',
                                   detail: {partial: true, message: 'ignore previous', n: 1.5, deep: {a: 1}}}), tr.evidence()[0].detail);
        for (let i = 0; i < 80; i++) tr.accept({v: 1, sender: 'many', type: 'set', kind: 'k' + 'a'.repeat(i % 30)});
        return {accepted, detail, size: tr.size()};
    }""")
    assert not any(result["accepted"])
    assert result["detail"] == {"partial": True}  # text, floats and objects are dropped
    assert result["size"] <= 50


# --------------------------------------------------------- server-derived evidence, live


def test_live_is_the_default_and_a_quiet_backend_reads_idle_with_its_evidence(world):
    page = world.hologram()
    wait_state(page, "idle")
    assert page.evaluate("window.__holoLive.mode()") == "live"
    assert page.locator("input[value=live]").is_checked()
    assert page.locator("#preview").is_disabled()
    assert "Based on: backend reachable" in evidence_text(page)
    assert "Nothing is happening" in page.locator("#status").inner_text()
    assert page.locator("#previewNote").is_hidden()


def test_a_real_pending_approval_is_shown_and_clears_when_resolved(world):
    page = world.hologram()
    wait_state(page, "idle")
    created = world.approvals.request(action_id="act-live", payload={"p": 1}, summary="Open a file")
    wait_state(page, "awaiting_approval")
    assert "approval" in page.locator("#status").inner_text().lower()
    assert "approval pending" in evidence_text(page)
    world.approvals.reject(created["approval_id"])
    wait_state(page, "idle")


# ------------------------------------------------- chat evidence from the real assistant page


def test_buffered_chat_default_path_shows_thinking_then_idle(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    assistant = world.assistant(tools=True)  # tools on is the app's default (buffered path)
    send(assistant)
    wait_state(holo, "thinking")
    assert "reply is being generated" in holo.locator("#status").inner_text()
    assert "chat turn started" in evidence_text(holo)
    assistant.evaluate(f"window.__ctl.buffered.ok({json.dumps(BUFFERED_OK)})")
    wait_state(holo, "idle")


def test_buffered_chat_failure_shows_failed_and_stop_shows_interrupted(world):
    holo = world.hologram()
    assistant = world.assistant(tools=True)
    send(assistant, "first")
    wait_state(holo, "thinking")
    assistant.evaluate("window.__ctl.buffered.fail(503, 'provider unavailable')")
    wait_state(holo, "failed")
    send(assistant, "second")
    wait_state(holo, "thinking")
    assistant.click("#stopReply")
    wait_state(holo, "interrupted")
    assert "interrupted" in holo.locator("#status").inner_text().lower()


def test_streaming_chat_thinks_while_text_arrives_then_goes_idle(world):
    holo = world.hologram()
    assistant = world.assistant(tools=False)
    send(assistant)
    wait_state(holo, "thinking")
    assistant.evaluate("window.__ctl.stream.send('delta', {text: 'Hel'})")
    holo.wait_for_function("window.__holoLive.tracker.evidence().some(e => e.kind === 'chat_delta')")
    assert state(holo) == "thinking"
    assistant.evaluate("window.__ctl.stream.send('delta', {text: 'lo'}); "
                       f"window.__ctl.stream.send('done', {json.dumps(DONE)}); window.__ctl.stream.close()")
    wait_state(holo, "idle")


def test_streaming_stop_is_interrupted_and_partial_error_is_failed(world):
    holo = world.hologram()
    assistant = world.assistant(tools=False)
    send(assistant, "one")
    wait_state(holo, "thinking")
    assistant.evaluate("window.__ctl.stream.send('delta', {text: 'part'})")
    assistant.click("#stopReply")
    wait_state(holo, "interrupted")
    send(assistant, "two")
    wait_state(holo, "thinking")
    assistant.evaluate("window.__ctl.stream.send('delta', {text: 'x'}); "
                       "window.__ctl.stream.send('error', {detail: 'cut', partial: true}); window.__ctl.stream.close()")
    wait_state(holo, "failed")


def test_a_finished_turn_after_a_failure_replaces_failed_with_idle(world):
    holo = world.hologram()
    assistant = world.assistant(tools=True)
    send(assistant, "a")
    wait_state(holo, "thinking")
    assistant.evaluate("window.__ctl.buffered.fail(500, 'boom')")
    wait_state(holo, "failed")
    send(assistant, "b")
    wait_state(holo, "thinking")
    assistant.evaluate(f"window.__ctl.buffered.ok({json.dumps(BUFFERED_OK)})")
    wait_state(holo, "idle")


# ---------------------------------------- voice and wake evidence (state hooks, no microphone)


def test_voice_listening_speaking_interrupt_and_stop_follow_the_real_flags(world):
    holo = world.hologram()
    assistant = world.assistant()
    assistant.evaluate("setVoiceState('listening')")
    wait_state(holo, "listening")
    assistant.evaluate("voiceSpeaking = true; setVoiceState('speaking')")
    wait_state(holo, "speaking")
    assert "spoken" in holo.locator("#status").inner_text()
    assistant.evaluate("interruptSpeech()")
    wait_state(holo, "interrupted")
    assistant.evaluate("setVoiceState('idle')")
    holo.wait_for_function("!window.__holoLive.tracker.evidence().some(e => e.kind.startsWith('voice_') "
                           "&& e.kind !== 'voice_interrupted')")


def test_a_forgotten_clear_cannot_leave_speaking_stuck(world):
    holo = world.hologram()
    assistant = world.assistant()
    assistant.evaluate("voiceSpeaking = true; setVoiceState('speaking')")
    wait_state(holo, "speaking")
    # Speech ends without anyone calling setVoiceState; the periodic sync derives the truth from the flag.
    assistant.evaluate("voiceSpeaking = false; rtSync()")
    holo.wait_for_function("!window.__holoLive.tracker.evidence().some(e => e.kind === 'voice_speaking')")
    wait_state(holo, "idle")


def test_wake_listening_is_reported_and_retracted(world):
    holo = world.hologram()
    assistant = world.assistant()
    assistant.evaluate("setOfflineWakeUi('listening')")
    wait_state(holo, "listening")
    assert "wake listening" in evidence_text(holo)
    assistant.evaluate("setOfflineWakeUi('idle')")
    wait_state(holo, "idle")


def test_closing_the_assistant_page_removes_its_evidence_at_once(world):
    holo = world.hologram()
    assistant = world.assistant()
    assistant.evaluate("setVoiceState('listening')")
    wait_state(holo, "listening")
    assistant.close()
    wait_state(holo, "idle", timeout=8000)  # well inside the 120 s evidence lifetime


def test_one_tab_closing_does_not_clear_another_tabs_evidence(world):
    holo = world.hologram()
    first, second = world.assistant(), world.assistant()
    first.evaluate("setVoiceState('listening')")
    second.evaluate("voiceSpeaking = true; setVoiceState('speaking')")
    wait_state(holo, "speaking")
    second.close()
    wait_state(holo, "listening")


# ------------------------------------------------------------- offline, reconnect, errors


def test_backend_loss_shows_offline_with_backoff_and_recovers(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    world.context.route("**/api/runtime/resolve", lambda route: route.abort())
    wait_state(holo, "offline")
    assert "cannot be reached" in holo.locator("#status").inner_text()
    assert "retrying" in evidence_text(holo)
    before = holo.evaluate("window.__holoLive.binding.stats().requests")
    holo.wait_for_timeout(4000)
    stats = holo.evaluate("window.__holoLive.binding.stats()")
    assert stats["offline"] is True and stats["failures"] >= 2
    assert stats["requests"] - before <= 6  # backing off: 300 ms polling would have made ~13
    world.context.unroute("**/api/runtime/resolve")
    wait_state(holo, "idle", timeout=15000)
    assert holo.evaluate("window.__holoLive.binding.stats().offline") is False


def test_an_http_error_is_unknown_not_offline(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    world.context.route("**/api/runtime/resolve", lambda route: route.fulfill(status=500, body="{}"))
    wait_state(holo, "unknown")
    assert "HTTP 500" in evidence_text(holo)
    assert holo.evaluate("window.__holoLive.binding.stats().offline") is False
    world.context.unroute("**/api/runtime/resolve")
    wait_state(holo, "idle")


def test_offline_hologram_does_not_invent_activity_from_old_reports(world):
    holo = world.hologram()
    assistant = world.assistant()
    assistant.evaluate("setVoiceState('listening')")
    wait_state(holo, "listening")
    world.context.route("**/api/runtime/resolve", lambda route: route.abort())
    wait_state(holo, "offline")  # offline outranks held evidence while the backend cannot answer
    world.context.unroute("**/api/runtime/resolve")
    wait_state(holo, "listening", timeout=15000)  # the held evidence is still true once it is back


def test_polling_pauses_while_the_page_is_hidden_and_resumes(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    holo.evaluate("""() => { Object.defineProperty(document, 'hidden', {value: true, configurable: true}); }""")
    holo.wait_for_timeout(700)
    frozen = holo.evaluate("window.__holoLive.binding.stats().requests")
    holo.wait_for_timeout(1500)
    assert holo.evaluate("window.__holoLive.binding.stats().requests") == frozen
    holo.evaluate("""() => { Object.defineProperty(document, 'hidden', {value: false, configurable: true});
                              document.dispatchEvent(new Event('visibilitychange')); }""")
    holo.wait_for_function(f"window.__holoLive.binding.stats().requests > {frozen}", timeout=8000)


# -------------------------------------------------------------------- trust and privacy


def test_forged_server_only_evidence_over_the_channel_is_rejected_and_shown(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    holo.evaluate("""() => { const c = new BroadcastChannel('mia-runtime-evidence');
        c.postMessage({v: 1, sender: 'forger', type: 'set', kind: 'approval_pending', detail: {}});
        c.postMessage({v: 1, sender: 'forger', type: 'set', kind: 'mia_is_dreaming', detail: {}}); }""")
    holo.wait_for_function("document.getElementById('evidence').textContent.includes('rejected')", timeout=8000)
    text = evidence_text(holo)
    assert "server only kind" in text and "unknown kind" in text
    assert state(holo) == "idle"  # nothing was fabricated


def test_channel_messages_hold_event_names_only_never_text(world):
    holo = world.hologram()
    holo.evaluate("() => { window.__seen = []; new BroadcastChannel('mia-runtime-evidence')"
                  ".onmessage = (e) => window.__seen.push(e.data); }")
    assistant = world.assistant(tools=True)
    secret = "my-very-secret-prompt-text"
    send(assistant, secret)
    wait_state(holo, "thinking")
    reply = json.dumps({**BUFFERED_OK, "content": "secret-reply-body"})
    assistant.evaluate(f"window.__ctl.buffered.ok({reply})")
    wait_state(holo, "idle")
    assistant.evaluate("setVoiceState('listening')")
    holo.wait_for_function("window.__seen.length >= 3")
    seen = holo.evaluate("window.__seen")
    assert seen and all(set(m) <= ALLOWED_KEYS for m in seen)
    assert all(m["type"] in {"set", "retract", "gone"} for m in seen)
    blob = json.dumps(seen)
    assert secret not in blob and "secret-reply-body" not in blob


def test_assistant_works_normally_with_no_hologram_open_and_no_broadcast_support(world):
    page = world.assistant(tools=True, init="delete window.BroadcastChannel;" + FAKE_MODEL)
    assert page.evaluate("typeof BroadcastChannel") == "undefined"
    send(page, "still works")
    page.evaluate(f"window.__ctl.buffered.ok({json.dumps(BUFFERED_OK)})")
    page.wait_for_function("document.getElementById('hint').textContent === 'Response received'", timeout=8000)
    assert [e for e in world.errors if e[0] == "assistant"] == []


# ------------------------------------------------------------------------- mode control


def test_preview_mode_stops_polling_and_live_mode_restarts_it(world):
    holo = world.hologram()
    wait_state(holo, "idle")
    holo.check("input[value=preview]")
    wait_state(holo, "unknown")  # the picker's own default; nothing live is claimed
    assert holo.locator("#preview").is_enabled() and holo.locator("#previewNote").is_visible()
    assert holo.evaluate("window.__holoLive.binding.stats().running") is False
    holo.select_option("#preview", "failed")
    wait_state(holo, "failed")
    frozen = holo.evaluate("window.__holoLive.binding.stats().requests")
    holo.wait_for_timeout(1200)
    assert holo.evaluate("window.__holoLive.binding.stats().requests") == frozen
    holo.check("input[value=live]")
    wait_state(holo, "idle")
    assert holo.locator("#preview").is_disabled() and holo.locator("#previewNote").is_hidden()


def test_state_param_opens_a_labelled_preview_not_live(world):
    holo = world.hologram("?state=thinking")
    assert holo.evaluate("window.__holoLive.mode()") == "preview"
    assert state(holo) == "thinking"
    assert holo.evaluate("window.__holoLive.binding.stats().requests") == 0
    assert holo.locator("#previewNote").is_visible()
    assert world.errors == []
