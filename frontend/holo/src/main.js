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

function show(state, { keepPicker = false } = {}) {
  const name = Object.hasOwn(STATE_LOOKS, state) ? state : 'unknown';
  if (!keepPicker) picker.value = name;
  current = name;
  statusEl.textContent = `${name.replace(/_/g, ' ')}: ${statements[name] || ''}`;
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

window.__holoLive = { tracker, binding, createTracker, mode: () => mode, setMode, hasChannel: Boolean(channel) };

// Wording only: the contract's statements are the source of truth when the backend answers.
fetch('/api/runtime/contract').then((r) => (r.ok ? r.json() : null)).then((c) => {
  if (c && c.statements) { statements = c.statements; show(current, { keepPicker: true }); }
}).catch(() => {});

setMode(mode, params.get('state') || 'unknown');
