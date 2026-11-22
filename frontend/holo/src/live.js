// Live binding for /hologram (MIA-HOLO-04). No 3D code here and no state rules: this collects the
// evidence other pages report, sends it with the owner's own server to POST /api/runtime/resolve,
// and hands back the snapshot. What a state *means* is decided by the HOLO-01 contract on the server.
export const CHANNEL_NAME = 'mia-runtime-evidence';
const KIND = /^[a-z][a-z_]{0,59}$/;
const MAX_ITEMS = 50;
const MAX_AGE_SECONDS = 600;

// Evidence held per (sender, kind). Pure apart from the injected clock, so it is testable.
export function createTracker(now = () => performance.now()) {
  const items = new Map();
  const key = (sender, kind) => `${sender}\u0000${kind}`;
  const valid = (m) => m && typeof m === 'object' && m.v === 1
    && typeof m.sender === 'string' && m.sender.length > 0 && m.sender.length <= 80;
  return {
    // Returns true when the message changed what is held. Malformed input is dropped, never guessed at.
    accept(message) {
      if (!valid(message)) return false;
      const { sender, type } = message;
      if (type === 'gone') {
        let removed = false;
        for (const k of [...items.keys()]) if (k.startsWith(`${sender}\u0000`)) { items.delete(k); removed = true; }
        return removed;
      }
      if (typeof message.kind !== 'string' || !KIND.test(message.kind)) return false;
      if (type === 'retract') return items.delete(key(sender, message.kind));
      if (type !== 'set') return false;
      if (!items.has(key(sender, message.kind)) && items.size >= MAX_ITEMS) return false;
      const detail = {};
      for (const [name, value] of Object.entries(message.detail && typeof message.detail === 'object' ? message.detail : {})) {
        if (typeof value === 'boolean' || Number.isInteger(value)) detail[String(name).slice(0, 20)] = value;
      }
      items.set(key(sender, message.kind), { kind: message.kind, at: now(), detail });
      return true;
    },
    // Newest assertion per kind, aged by how long ago this page received it (device clocks never compared).
    evidence() {
      const t = now();
      const newest = new Map();
      for (const item of items.values()) {
        const age = Math.max(0, (t - item.at) / 1000);
        if (age > MAX_AGE_SECONDS) continue;
        const have = newest.get(item.kind);
        if (!have || age < have.age_seconds) newest.set(item.kind, { kind: item.kind, age_seconds: Math.round(age * 10) / 10, detail: item.detail });
      }
      return [...newest.values()];
    },
    size: () => items.size,
    clear: () => items.clear(),
  };
}

// Polls the resolver. Offline means the request itself failed; an HTTP error means the backend answered
// but could not resolve, which is reported as unknown, not as offline.
export function createLiveBinding({
  tracker, onSnapshot, onOffline, onError, fetchImpl = (...a) => fetch(...a),
  baseMs = 2000, maxBackoffMs = 10000, timeoutMs = 5000, isHidden = () => document.hidden,
  setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id),
}) {
  let timer = 0;
  let running = false;
  let inflight = null;
  let failures = 0;
  const stats = { requests: 0, offline: false, nextInMs: 0, lastOk: null };

  async function poll() {
    timer = 0;
    if (!running) return;
    if (isHidden()) { schedule(baseMs); return; } // do not spend requests while nobody can see the page
    const controller = new AbortController();
    inflight = controller;
    const guard = setTimer(() => controller.abort(), timeoutMs);
    stats.requests += 1;
    try {
      const response = await fetchImpl('/api/runtime/resolve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ evidence: tracker.evidence() }), signal: controller.signal,
      });
      if (!running) return;
      if (!response.ok) {
        failures = 0; stats.offline = false;
        onError(`The backend answered with HTTP ${response.status}.`);
      } else {
        const snapshot = await response.json();
        if (!running) return;
        failures = 0; stats.offline = false; stats.lastOk = Date.now();
        onSnapshot(snapshot);
      }
    } catch (error) {
      if (!running) return;
      failures += 1; stats.offline = true;
      onOffline(failures);
    } finally {
      clearTimer(guard);
      if (inflight === controller) inflight = null;
    }
    if (running) schedule(stats.offline ? Math.min(maxBackoffMs, baseMs * 2 ** Math.min(failures, 5)) : baseMs);
  }

  function schedule(ms) {
    if (!running) return;
    if (timer) clearTimer(timer);
    stats.nextInMs = ms;
    timer = setTimer(poll, ms);
  }

  return {
    start() { if (running) return; running = true; schedule(0); },
    stop() { running = false; if (timer) clearTimer(timer); timer = 0; if (inflight) inflight.abort(); },
    // Something changed (a message arrived, the page became visible): look now, but never in parallel.
    nudge(ms = 120) { if (running && !inflight) schedule(ms); },
    stats: () => ({ ...stats, failures, running, inflight: Boolean(inflight) }),
  };
}
