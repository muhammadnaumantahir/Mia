import { createHoloRenderer, STATE_LOOKS } from './renderer.js';

const canvas = document.getElementById('scene');
const fallback = document.getElementById('fallback');
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const holo = createHoloRenderer(canvas, { reducedMotion });

function showFallback(reason) {
  canvas.hidden = true;
  fallback.hidden = false;
  fallback.dataset.reason = reason;
}

if (!holo.ok) {
  showFallback(holo.reason);
  window.__holo = { ok: false, reason: holo.reason };
} else {
  const fit = () => holo.resize(canvas.clientWidth || 320, canvas.clientHeight || 320, devicePixelRatio);
  addEventListener('resize', fit);
  fit();
  holo.setState(new URLSearchParams(location.search).get('state') || 'unknown');
  holo.start();
  window.__holo = { ok: true, holo, states: Object.keys(STATE_LOOKS) };
}
