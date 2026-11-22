// Page glue for /hologram. Shows one state at a time. In HOLO-03 the state comes from the URL
// or the preview selector only; binding it to live events is MIA-HOLO-04.
import { createHoloRenderer, STATE_LOOKS, poseAt } from './renderer.js';
import { CHANNEL_NAME, createTracker, createLiveBinding } from './live.js';

const FALLBACK_STATEMENTS = {
  unknown: "Mia's current activity is not known.",
  idle: 'Nothing is happening that the app can see.',
  listening: 'A listening session is open on this device.',
  thinking: 'A reply is being generated.',
  speaking: 'A reply is being spoken.',
  interrupted: 'The last activity was interrupted.',
  tool_running: 'A computer task is running.',
  awaiting_approval: "An action is waiting for the owner's approval.",
  failed: 'The last activity failed.',
  offline: 'The Mia backend cannot be reached.',
};

// One distinct outline per state, so the non-3D view never relies on colour (or motion) alone.
const GLYPHS = {
  unknown: '<circle cx="50" cy="50" r="36" stroke-dasharray="4 10"/><path d="M40 40q0-12 10-12t10 10q0 8-10 12v6M50 70v2"/>',
  idle: '<circle cx="50" cy="50" r="36"/>',
  listening: '<circle cx="50" cy="50" r="12"/><circle cx="50" cy="50" r="26"/><circle cx="50" cy="50" r="40"/>',
  thinking: '<circle cx="28" cy="50" r="7"/><circle cx="50" cy="50" r="7"/><circle cx="72" cy="50" r="7"/>',
  speaking: '<path d="M14 50h8M30 36v28M42 24v52M54 32v36M66 40v20M78 46v8"/>',
  interrupted: '<circle cx="50" cy="50" r="36"/><path d="M40 36v28M60 36v28"/>',
  tool_running: '<path d="M50 14l8 8 12-2 2 12 12 8-8 10 4 12-12 4-6 12-12-6-12 6-6-12-12-4 4-12-8-10 12-8 2-12 12 2z"/><circle cx="50" cy="50" r="10"/>',
  awaiting_approval: '<path d="M50 12l36 26v34H14V38z"/><path d="M38 52l9 9 17-18"/>',
  failed: '<path d="M50 12l40 72H10z"/><path d="M50 40v22M50 72v2"/>',
  offline: '<circle cx="50" cy="50" r="36"/><path d="M24 76L76 24"/>',
};

const canvas = document.getElementById('scene');
const fallback = document.getElementById('fallback');
const statusEl = document.getElementById('status');
const picker = document.getElementById('preview');
const stage = document.getElementById('stage');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let statements = FALLBACK_STATEMENTS;
let current = 'unknown';

for (const name of Object.keys(STATE_LOOKS)) {
  const option = document.createElement('option');
  option.value = name;
  option.textContent = name.replace(/_/g, ' ');
  picker.appendChild(option);
}

const holo = createHoloRenderer(canvas, { reducedMotion });

const glyphView = document.getElementById('glyphView');
const glyphSvg = document.getElementById('glyph');
const glyphName = document.getElementById('glyphName');
let view = '3d';
let lastStatus = '';

function renderGlyph(name) {
  glyphSvg.innerHTML = GLYPHS[name] || GLYPHS.unknown;
  glyphName.textContent = name.replace(/_/g, ' ');
}

function show(state, { keepPicker = false } = {}) {
  const name = Object.hasOwn(STATE_LOOKS, state) ? state : 'unknown';
  if (!keepPicker) picker.value = name;
  current = name;
  // Only write when the sentence changes, so a screen reader is not re-announced on every poll.
  const sentence = `${name.replace(/_/g, ' ')}: ${statements[name] || ''}`;
  if (sentence !== lastStatus) { statusEl.textContent = sentence; lastStatus = sentence; }
  renderGlyph(name);
  stage.dataset.state = name;
  if (holo.ok) holo.setState(name);
}


if (!holo.ok) {
  canvas.hidden = true;
  fallback.hidden = false;
  fallback.dataset.reason = holo.reason;
  window.__holo = { ok: false, reason: holo.reason, show, states: Object.keys(STATE_LOOKS), poseAt, current: () => current };
} else {
  const fit = () => {
    const box = stage.getBoundingClientRect();
    const size = Math.max(120, Math.floor(Math.min(box.width, box.height || box.width)));
    holo.resize(size, size, devicePixelRatio);
  };
  new ResizeObserver(fit).observe(stage);
  fit();
  holo.start();
  window.__holo = { ok: true, holo, show, states: Object.keys(STATE_LOOKS), poseAt, current: () => current };
}

function setView(next) {
  view = next === 'text' || !holo.ok ? 'text' : '3d';
  glyphView.hidden = view !== 'text';
  canvas.hidden = view === 'text';
  document.querySelectorAll('input[name="view"]').forEach((input) => { input.checked = input.value === view; });
  // A hidden 3D view must not keep drawing.
  if (holo.ok) { if (view === 'text') holo.stop(); else { holo.start(); } }
  stage.dataset.view = view;
}
document.querySelectorAll('input[name="view"]').forEach((input) => {
  input.addEventListener('change', () => { if (input.checked) setView(input.value); });
});

picker.addEventListener('change', () => { if (mode === 'preview') show(picker.value); });

const params = new URLSearchParams(location.search);
// ?state=X or ?mode=preview selects the owner-driven preview; otherwise the page is live.
let mode = (params.has('state') || params.get('mode') === 'preview') ? 'preview' : 'live';
const tracker = createTracker();
const evidenceEl = document.getElementById('evidence');
const modeInputs = [...document.querySelectorAll('input[name="mode"]')];
const pollMs = Math.max(200, Number(params.get('poll')) || 2000);
let liveSince = '';

function describeEvidence(snapshot) {
  const bits = [];
  if (snapshot.reason) {
    const used = (snapshot.evidence_used || [])[0];
    const age = used ? Math.max(0, Math.round((Date.parse(snapshot.as_of) - Date.parse(used.observed_at)) / 1000)) : 0;
    bits.push(`Based on: ${snapshot.reason.replace(/_/g, ' ')}, ${age} s ago`);
  } else {
    bits.push('No fresh evidence of any activity');
  }
  if (snapshot.stale && snapshot.stale.length) bits.push(`ignored as stale: ${snapshot.stale.join(', ').replace(/_/g, ' ')}`);
  if (snapshot.rejected && snapshot.rejected.length) bits.push(`rejected: ${snapshot.rejected.map((r) => r.reason).join(', ').replace(/_/g, ' ')}`);
  return bits.join(' · ');
}

const binding = createLiveBinding({
  tracker, baseMs: pollMs,
  onSnapshot(snapshot) {
    if (mode !== 'live') return;
    if (snapshot.statement) statements = { ...statements, [snapshot.state]: snapshot.statement };
    show(snapshot.state, { keepPicker: true });
    liveSince = new Date().toLocaleTimeString();
    evidenceEl.textContent = `${describeEvidence(snapshot)} · checked ${liveSince}`;
  },
  onOffline(failures) {
    if (mode !== 'live') return;
    show('offline', { keepPicker: true });
    const wait = Math.round(binding.stats().nextInMs / 1000) || 1;
    evidenceEl.textContent = `The Mia backend did not answer (attempt ${failures}); retrying in about ${wait} s.`;
  },
  onError(message) {
    if (mode !== 'live') return;
    show('unknown', { keepPicker: true });
    evidenceEl.textContent = `${message} The state cannot be resolved right now.`;
  },
});

function setMode(next, previewState) {
  mode = next === 'preview' ? 'preview' : 'live';
  modeInputs.forEach((input) => { input.checked = input.value === mode; });
  picker.disabled = mode !== 'preview';
  document.getElementById('previewNote').hidden = mode !== 'preview';
  if (mode === 'live') {
    show('unknown', { keepPicker: true });
    evidenceEl.textContent = 'Checking Mia…';
    binding.start();
    binding.nudge(0);
  } else {
    binding.stop();
    evidenceEl.textContent = '';
    show(previewState || picker.value);
  }
}
modeInputs.forEach((input) => input.addEventListener('change', () => { if (input.checked) setMode(input.value); }));

let channel = null;
try {
  if ('BroadcastChannel' in window) {
    channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = (event) => { if (tracker.accept(event.data) && mode === 'live') binding.nudge(); };
  }
} catch (error) { channel = null; }
document.addEventListener('visibilitychange', () => { if (!document.hidden && mode === 'live') binding.nudge(0); });
addEventListener('pagehide', () => { binding.stop(); if (channel) channel.close(); });

window.__holoView = { setView, view: () => view, glyphs: Object.keys(GLYPHS) };
window.__holoLive = { tracker, binding, createTracker, mode: () => mode, setMode, hasChannel: Boolean(channel) };

// Wording only: the contract's statements are the source of truth when the backend answers.
fetch('/api/runtime/contract').then((r) => (r.ok ? r.json() : null)).then((c) => {
  if (c && c.statements) { statements = c.statements; show(current, { keepPicker: true }); }
}).catch(() => {});

// 3D by default; the text view when asked (?view=text) or when 3D cannot run. Reduced motion keeps
// the 3D view but draws it still (the renderer does that), so the text view is a choice, not a penalty.
setView(params.get('view') === 'text' ? 'text' : '3d');
setMode(mode, params.get('state') || 'unknown');
