// Page glue for /hologram. Shows one state at a time. In HOLO-03 the state comes from the URL
// or the preview selector only; binding it to live events is MIA-HOLO-04.
import { createHoloRenderer, STATE_LOOKS, poseAt } from './renderer.js';

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

for (const name of Object.keys(STATE_LOOKS)) {
  const option = document.createElement('option');
  option.value = name;
  option.textContent = name.replace(/_/g, ' ');
  picker.appendChild(option);
}

const holo = createHoloRenderer(canvas, { reducedMotion });

function show(state) {
  const name = Object.hasOwn(STATE_LOOKS, state) ? state : 'unknown';
  picker.value = name;
  statusEl.textContent = `${name.replace(/_/g, ' ')}: ${statements[name] || ''}`;
  stage.dataset.state = name;
  if (holo.ok) holo.setState(name);
}

picker.addEventListener('change', () => show(picker.value));

if (!holo.ok) {
  canvas.hidden = true;
  fallback.hidden = false;
  fallback.dataset.reason = holo.reason;
  window.__holo = { ok: false, reason: holo.reason, show, states: Object.keys(STATE_LOOKS), poseAt };
} else {
  const fit = () => {
    const box = stage.getBoundingClientRect();
    const size = Math.max(120, Math.floor(Math.min(box.width, box.height || box.width)));
    holo.resize(size, size, devicePixelRatio);
  };
  new ResizeObserver(fit).observe(stage);
  fit();
  holo.start();
  window.__holo = { ok: true, holo, show, states: Object.keys(STATE_LOOKS), poseAt };
}

show(new URLSearchParams(location.search).get('state') || 'unknown');

// Wording only: the contract's statements are the source of truth when the backend answers.
fetch('/api/runtime/contract').then((r) => (r.ok ? r.json() : null)).then((c) => {
  if (c && c.statements) { statements = c.statements; show(picker.value); }
}).catch(() => {});
