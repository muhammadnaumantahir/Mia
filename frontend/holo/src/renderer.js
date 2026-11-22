// The only file that knows the 3D library (decision: docs/architecture/HOLO_RENDERER_DECISION.md).
// Isolated from Brain/business logic: it takes a state NAME and draws. It never fetches,
// never reads app data and never decides what state Mia is in (that is the HOLO-01 contract).
import {
  WebGLRenderer, Scene, PerspectiveCamera, Mesh, IcosahedronGeometry, TorusGeometry,
  OctahedronGeometry, MeshBasicMaterial, Color,
} from 'three';

// One look per HOLO-01 state. Colour is never the only cue: each state also differs in
// motion (spin, pulse rate/depth, ring tilt) and in core shape (failed/offline are angular).
// A look never claims more than the state's own statement.
export const STATE_LOOKS = {
  unknown:           { color: 0x667788, spin: 0.0, pulseHz: 0.0, pulseDepth: 0.0,  ring: 0.0, opacity: 0.35, shape: 'sphere' },
  idle:              { color: 0x44ddee, spin: 0.15, pulseHz: 0.2, pulseDepth: 0.02, ring: 0.1, opacity: 0.55, shape: 'sphere' },
  listening:         { color: 0x55ffaa, spin: 0.3, pulseHz: 0.8, pulseDepth: 0.06, ring: 0.3, opacity: 0.8, shape: 'sphere' },
  thinking:          { color: 0xaa88ff, spin: 0.9, pulseHz: 0.0, pulseDepth: 0.0,  ring: 1.6, opacity: 0.8, shape: 'sphere' },
  speaking:          { color: 0x66ccff, spin: 0.5, pulseHz: 2.4, pulseDepth: 0.09, ring: 0.5, opacity: 0.95, shape: 'sphere' },
  interrupted:       { color: 0xffcc55, spin: 0.05, pulseHz: 0.0, pulseDepth: 0.0, ring: 0.0, opacity: 0.6, shape: 'sphere' },
  tool_running:      { color: 0x55aaff, spin: 0.7, pulseHz: 0.0, pulseDepth: 0.0,  ring: -2.2, opacity: 0.85, shape: 'sphere' },
  awaiting_approval: { color: 0xffaa33, spin: 0.0, pulseHz: 1.1, pulseDepth: 0.05, ring: 0.0, opacity: 1.0, shape: 'sphere' },
  failed:            { color: 0xff5566, spin: 0.0, pulseHz: 0.0, pulseDepth: 0.0,  ring: 0.0, opacity: 0.9, shape: 'angular' },
  offline:           { color: 0x555555, spin: 0.0, pulseHz: 0.0, pulseDepth: 0.0,  ring: 0.0, opacity: 0.25, shape: 'angular' },
};

// Pure: where the core is at animation time t (seconds), for a look. Testable without WebGL.
export function poseAt(look, t) {
  return {
    scale: 1 + look.pulseDepth * Math.sin(2 * Math.PI * look.pulseHz * t),
    coreY: look.spin * t,
    ringZ: look.ring * t,
  };
}

export function createHoloRenderer(canvas, options = {}) {
  const { reducedMotion = false, maxFps = 30, onContextLost = () => {}, onContextRestored = () => {} } = options;

  let renderer;
  try {
    renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  } catch (error) {
    return { ok: false, reason: 'webgl_unavailable', detail: String((error && error.message) || error) };
  }

  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.1, 50);
  camera.position.z = 4;
  const material = new MeshBasicMaterial({ wireframe: true, transparent: true });
  const geometries = { sphere: new IcosahedronGeometry(1, 2), angular: new OctahedronGeometry(1, 0) };
  const core = new Mesh(geometries.sphere, material);
  const ringGeometry = new TorusGeometry(1.5, 0.01, 8, 96);
  const ring = new Mesh(ringGeometry, material);
  ring.rotation.x = Math.PI / 2.4;
  scene.add(core, ring);

  const stats = { frames: 0, state: 'unknown', running: false, contextLost: false, paused: false, t: 0 };
  let look = STATE_LOOKS.unknown;
  let handle = 0;
  let last = 0;
  const minInterval = 1000 / Math.max(1, maxFps);

  function apply() {
    material.color = new Color(look.color);
    material.opacity = look.opacity;
    core.geometry = geometries[look.shape];
  }

  function draw() {
    const pose = poseAt(look, stats.t);
    core.scale.setScalar(pose.scale);
    core.rotation.y = pose.coreY;
    ring.rotation.z = pose.ringZ;
    renderer.render(scene, camera);
    stats.frames += 1;
  }

  // One animation step of dt seconds. The loop calls this; tests call it directly.
  function step(dt) {
    if (stats.contextLost) return;
    stats.t += Math.max(0, Math.min(dt, 0.25)); // a long stall must not teleport the animation
    draw();
  }

  function loop(now) {
    handle = 0;
    if (!stats.running || stats.contextLost || document.hidden) {
      stats.paused = stats.running && document.hidden;
      return;
    }
    stats.paused = false;
    if (now - last >= minInterval) {
      const dt = last ? (now - last) / 1000 : 0;
      last = now;
      step(dt);
    }
    handle = requestAnimationFrame(loop);
  }

  function schedule() {
    if (!handle && stats.running && !reducedMotion) {
      last = 0; // do not count time spent paused or hidden as animation time
      handle = requestAnimationFrame(loop);
    }
  }

  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault();
    stats.contextLost = true;
    onContextLost();
  });
  canvas.addEventListener('webglcontextrestored', () => {
    stats.contextLost = false;
    apply();
    onContextRestored();
    if (reducedMotion) draw(); else schedule();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(); });

  apply();
  return {
    ok: true,
    setState(state) {
      stats.state = Object.hasOwn(STATE_LOOKS, state) ? state : 'unknown'; // unrecognised (incl. 'constructor') -> unknown, never idle
      look = STATE_LOOKS[stats.state];
      apply();
      if ((reducedMotion || !stats.running) && !stats.contextLost) draw();
    },
    resize(width, height, pixelRatio = 1) {
      renderer.setPixelRatio(Math.min(pixelRatio, 2));
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
      if ((reducedMotion || !stats.running) && !stats.contextLost) draw();
    },
    start() {
      stats.running = true;
      if (reducedMotion) draw(); else schedule();
    },
    stop() { stats.running = false; if (handle) cancelAnimationFrame(handle); handle = 0; },
    step,
    dispose() {
      this.stop();
      Object.values(geometries).forEach((g) => g.dispose());
      ringGeometry.dispose(); material.dispose(); renderer.dispose();
    },
    stats() {
      const info = renderer.info;
      return { ...stats, shape: look.shape, drawCalls: info.render.calls, triangles: info.render.triangles,
               scale: core.scale.x, coreY: core.rotation.y, ringZ: ring.rotation.z };
    },
  };
}
