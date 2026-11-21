"""MIA-UX-05: microphone visibility and stop safety for the one-shot Record button.

Product rule: the microphone is opt-in, visible, and immediately stoppable. These tests run the
page's real ``startRecording`` under Node with a fake microphone, recorder and a transcription
request that stays pending, and check the page's source for the Esc and page-hide paths.
JS logic only; this is not a real-browser or real-microphone test.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "src" / "mia" / "assistant.html"


def source() -> str:
    return PAGE.read_text(encoding="utf-8")


def region(start: str, end: str) -> str:
    text = source()
    i = text.index(start)
    return text[i: text.index(end, i)]


def test_escape_stops_recording_before_anything_else_and_page_hide_releases_the_mic():
    page = source()
    keys = region("document.addEventListener('keydown',event=>{", "\n});")
    assert keys.index("recording&&mediaRecorder") < keys.index("voiceSpeaking")
    assert "mediaRecorder.stop()" in keys
    hide = region("window.addEventListener('pagehide'", "\n});")
    assert "recording&&mediaRecorder" in hide and "releaseRecordingStream()" in hide
    assert "setMicBadge(false)" in hide
    # the transcription request must not be a precondition for releasing the microphone
    assert page.count("releaseRecordingStream()") >= 5


def test_record_button_exposes_its_state_to_assistive_technology():
    page = source()
    tag = page[page.rindex("<", 0, page.index('id="mic"')): page.index(">", page.index('id="mic"')) + 1]
    assert 'aria-pressed="false"' in tag


HARNESS = r"""
const assert = require('assert');
const els = {};
const el = (id) => (els[id] = {id, value: '', textContent: '', disabled: false, attrs: {}, dataset: {},
  setAttribute(k, v) { this.attrs[k] = v; }, focus() {}});
['mic', 'hint', 'prompt'].forEach(el);
const $ = (id) => els[id];

let activeStream = null, mediaRecorder = null, audioChunks = [], recording = false;
const voiceMode = false;
const badge = {active: false, label: ''};
function setMicBadge(active, label) { badge.active = active; badge.label = label || ''; }
function micConstraints() { return {audio: true}; }
function refreshMicDevices() {}

let tracks = [];
let rejectMic = false;
Object.defineProperty(global, 'navigator', {configurable: true, value: {mediaDevices: {getUserMedia: async () => {
  if (rejectMic) throw new Error('Permission denied');
  const track = {live: true, stop() { this.live = false; }};
  tracks.push(track);
  return {getTracks: () => [track]};
}}}});
class FakeRecorder {
  constructor(stream) { this.stream = stream; this.mimeType = 'audio/webm'; mediaRecorder = this; }
  static isTypeSupported() { return true; }
  start() {}
  stop() {  // browsers fire the final dataavailable, then stop
    this.ondataavailable({data: {size: 3}});
    setTimeout(() => this.onstop(), 0);
  }
}
global.MediaRecorder = FakeRecorder;
global.window = global;
let releaseTranscription;
const transcription = new Promise(r => { releaseTranscription = r; });
global.fetch = () => transcription;   // stays pending until the test releases it
const tick = () => new Promise(r => setTimeout(r, 15));
__SOURCE__
(async () => {
  const out = {};
  // 1. Start: the visible badge and button state say the microphone is on.
  await startRecording();
  out.started = {recording, badge: {...badge}, pressed: $('mic').attrs['aria-pressed'],
                 live: tracks.map(t => t.live), hint: $('hint').textContent};

  // 2. Stop: tracks are released and the badge is off WHILE transcription is still pending.
  mediaRecorder.stop();
  await tick();
  out.stopped_while_transcribing = {recording, badge: {...badge}, pressed: $('mic').attrs['aria-pressed'],
                                    live: tracks.map(t => t.live), hint: $('hint').textContent,
                                    micDisabled: $('mic').disabled};
  releaseTranscription({ok: true, json: async () => ({text: 'hello'})});
  await tick();
  out.after_transcription = {prompt: $('prompt').value, micDisabled: $('mic').disabled, activeStream};

  // 3. Permission failure: no stuck badge, nothing left open.
  tracks = []; rejectMic = true;
  await startRecording();
  out.denied = {recording, badge: {...badge}, pressed: $('mic').attrs['aria-pressed'],
                tracks: tracks.length, hint: $('hint').textContent};
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || String(e)); process.exit(1); });
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_real_start_recording_shows_mic_state_and_releases_it_before_transcription(tmp_path):
    code = region("function releaseRecordingStream(){", "\n$('mic').addEventListener('click'")
    script = tmp_path / "mic.js"
    script.write_text(HARNESS.replace("__SOURCE__", code), encoding="utf-8")
    run = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr + run.stdout
    out = json.loads(run.stdout.strip().splitlines()[-1])

    started = out["started"]
    assert started["recording"] is True
    assert started["badge"] == {"active": True, "label": "Recording"}
    assert started["pressed"] == "true" and started["live"] == [True]
    assert "Esc" in started["hint"]

    stopped = out["stopped_while_transcribing"]
    assert stopped["recording"] is False
    assert stopped["badge"]["active"] is False
    assert stopped["pressed"] == "false"
    assert stopped["live"] == [False]  # released before the transcription request finished
    assert "Transcribing" in stopped["hint"] and stopped["micDisabled"] is True

    assert out["after_transcription"]["prompt"] == "hello"
    assert out["after_transcription"]["micDisabled"] is False
    assert out["after_transcription"]["activeStream"] is None

    denied = out["denied"]
    assert denied["recording"] is False and denied["badge"]["active"] is False
    assert denied["pressed"] == "false" and denied["tracks"] == 0
    assert "Permission denied" in denied["hint"]
