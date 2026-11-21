"""MIA-UX-01: the owner can stop a text reply that is still streaming.

Two layers of evidence:
* source-contract checks on assistant.html (same style as test_voice_ui_contract.py);
* a behavioural harness that extracts the *real* ``streamReply`` function from the page and
  runs it under Node against a fake streaming ``fetch`` that honours ``AbortSignal``.
  The harness is skipped when Node is unavailable. It exercises the JS logic only; it is
  not a real-browser test (owner/browser verification remains separate).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ASSISTANT_PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"


def source() -> str:
    return ASSISTANT_PAGE.read_text(encoding="utf-8")


def function_source(name: str) -> str:
    text = source()
    start = text.index(f"async function {name}(")
    end = text.index("\nlet mediaRecorder=null;", start)
    return text[start:end]


def test_stop_button_is_hidden_until_a_reply_is_in_flight_and_is_labelled():
    page = source()
    assert 'id="stopReply"' in page
    tag = page[page.index('<button class="send stop" id="stopReply"'):]
    tag = tag[: tag.index("</button>")]
    assert " hidden" in tag and 'type="button"' in tag
    assert "aria-label=" in tag and "Esc" in tag
    assert ".send.stop[hidden]{display:none}" in page


def test_submit_flow_wires_abort_signal_to_both_request_paths_and_cleans_up():
    page = source()
    start = page.index("async function submitPrompt(")
    end = page.index("\n$('composer').addEventListener", start)
    flow = page[start:end]
    assert "chatAbort=new AbortController()" in flow
    assert "signal:chatSignal" in flow  # buffered/tool path
    assert "bubble,chatSignal)" in flow  # streaming path
    assert "$('stopReply').hidden=false;" in flow
    final = flow[flow.index("}finally{"):]
    assert "chatAbort=null" in final and "$('stopReply').hidden=true;" in final
    # a partial reply is never spoken as if it were complete
    aborted = flow[flow.index("if(streamed.aborted){"): flow.index("if(streamed.done){")]
    assert "return;" in aborted and "speakReply" not in aborted
    assert "partial reply" in aborted


def test_escape_stops_generation_only_after_speech_interruption_takes_priority():
    page = source()
    speech = "if(event.key==='Escape'&&voiceSpeaking)"
    gen = "if(event.key==='Escape'&&chatBusy&&chatAbort)"
    assert speech in page and gen in page
    assert page.index(speech) < page.index(gen)
    assert 'id="hint" role="status" aria-live="polite"' in page


def test_stop_button_click_aborts_the_active_request():
    page = source()
    assert "$('stopReply').addEventListener('click',()=>{if(chatAbort)chatAbort.abort();});" in page


HARNESS = r"""
const assert = require('assert');
__STREAM_SOURCE__

function sse(name, payload) { return new TextEncoder().encode(`event: ${name}\ndata: ${JSON.stringify(payload)}\n\n`); }

// Fake streaming fetch. `chunks` are delivered one per read(); then the stream either ends
// or stays open until the AbortSignal fires (rejecting with an AbortError like a browser).
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
function makeBubble() {
  return {textContent: '', kids: [], appendChild(n) { this.kids.push(n); }, scrollIntoView() {}};
}
global.document = {createElement: () => ({style: {}, textContent: ''})};

(async () => {
  const out = {};

  // 1. Stop mid-stream: partial text is kept and flagged as aborted; no exception escapes.
  {
    const controller = new AbortController(); const bubble = makeBubble();
    global.fetch = fakeFetch({chunks: [sse('delta', {text: 'Hello '}), sse('delta', {text: 'wor'})], hang: true});
    const pending = streamReply({prompt: 'hi'}, bubble, controller.signal);
    await new Promise(r => setTimeout(r, 20));
    controller.abort();
    const result = await pending;
    assert.strictEqual(result.aborted, true);
    assert.strictEqual(result.done, null);
    assert.strictEqual(result.text, 'Hello wor');
    assert.strictEqual(bubble.textContent, 'Hello wor');
    out.mid_stream = result;
  }

  // 2. Stop before the response headers arrive.
  {
    const controller = new AbortController(); controller.abort();
    global.fetch = fakeFetch({chunks: []});
    const result = await streamReply({prompt: 'hi'}, makeBubble(), controller.signal);
    assert.strictEqual(result.aborted, true); assert.strictEqual(result.text, '');
    out.before_response = result;
  }

  // 3. Normal completion is unchanged: no abort flag, done payload returned.
  {
    const bubble = makeBubble();
    global.fetch = fakeFetch({chunks: [sse('delta', {text: 'Done.'}), sse('done', {conversation_id: 'c1', provider_id: 'p', model_id: 'm'})]});
    const result = await streamReply({prompt: 'hi'}, bubble, new AbortController().signal);
    assert.ok(!result.aborted); assert.strictEqual(result.done.conversation_id, 'c1');
    assert.strictEqual(result.text, 'Done.');
    out.normal = {text: result.text, aborted: !!result.aborted};
  }

  // 4. Not passing a signal still works (backwards compatible call shape).
  {
    global.fetch = fakeFetch({chunks: [sse('delta', {text: 'x'}), sse('done', {conversation_id: 'c2'})]});
    const result = await streamReply({prompt: 'hi'}, makeBubble());
    assert.ok(!result.aborted); assert.strictEqual(result.done.conversation_id, 'c2');
    out.no_signal = true;
  }

  // 5. Genuine failures are NOT mistaken for a user stop.
  {
    global.fetch = fakeFetch({chunks: [], ok: false, status: 503});
    await assert.rejects(() => streamReply({prompt: 'hi'}, makeBubble(), new AbortController().signal),
                         /provider unavailable/);
    global.fetch = fakeFetch({chunks: [sse('delta', {text: 'a'}), sse('error', {detail: 'boom', partial: false})]});
    await assert.rejects(() => streamReply({prompt: 'hi'}, makeBubble(), new AbortController().signal), /boom/);
    out.failures_still_throw = true;
  }

  console.log(JSON.stringify(out));
})().catch(error => { console.error(error && error.stack || String(error)); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_real_stream_reply_function_keeps_partial_text_and_flags_abort(tmp_path):
    script = tmp_path / "harness.js"
    script.write_text(HARNESS.replace("__STREAM_SOURCE__", function_source("streamReply")), encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    outcome = json.loads(run.stdout.strip().splitlines()[-1])
    assert outcome["mid_stream"]["aborted"] is True
    assert outcome["mid_stream"]["text"] == "Hello wor"
    assert outcome["before_response"]["text"] == ""
    assert outcome["normal"] == {"text": "Done.", "aborted": False}
    assert outcome["no_signal"] and outcome["failures_still_throw"]
