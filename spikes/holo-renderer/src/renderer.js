// MIA-HOLO-02 spike: the only file that knows about the 3D library.
// Everything else talks to the small interface returned by createHoloRenderer(),
// so swapping the library later touches this file only.
import {
  WebGLRenderer, Scene, PerspectiveCamera, Mesh, IcosahedronGeometry, TorusGeometry,
  MeshBasicMaterial, Color,
} from 'three';

// Visual treatment per HOLO-01 runtime state. Purely presentational: a look never
// claims more than the state's own statement (e.g. "thinking" is not shown as speech).
export const STATE_LOOKS = {
  unknown: { color: 0x667788, speed: 0.0, opacity: 0.35 },
  idle: { color: 0x44ddee, speed: 0.15, opacity: 0.55 },
  listening: { color: 0x55ffaa, speed: 0.3, opacity: 0.8 },
  thinking: { color: 0xaa88ff, speed: 0.9, opacity: 0.8 },
  speaking: { color: 0x66ccff, speed: 0.6, opacity: 0.95 },
  interrupted: { color: 0xffcc55, speed: 0.1, opacity: 0.6 },
  tool_running: { color: 0x55aaff, speed: 0.7, opacity: 0.85 },
  awaiting_approval: { color: 0xffaa33, speed: 0.0, opacity: 1.0 },
  failed: { color: 0xff5566, speed: 0.0, opacity: 0.9 },
  offline: { color: 0x555555, speed: 0.0, opacity: 0.25 },
};

export function createHoloRenderer(canvas, options = {}) {
  const { reducedMotion = false, maxFps = 30, onContextLost = () => {} } = options;

  let renderer;
  try {
    renderer = new WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  } catch (error) {
    return { ok: false, reason: 'webgl_unavailable', detail: String(error && error.message || error) };
  }

  const scene = new Scene();
  const camera = new PerspectiveCamera(45, 1, 0.1, 50);
  camera.position.z = 4;
  const material = new MeshBasicMaterial({ wireframe: true, transparent: true });
  const core = new Mesh(new IcosahedronGeometry(1, 2), material);
  const ring = new Mesh(new TorusGeometry(1.5, 0.01, 8, 96), material);
  ring.rotation.x = Math.PI / 2.4;
  scene.add(core, ring);

  const stats = { frames: 0, state: 'unknown', running: false, contextLost: false, paused: false };
  let look = STATE_LOOKS.unknown;
  let handle = 0;
  let last = 0;
  const minInterval = 1000 / Math.max(1, maxFps);

  function apply() {
    material.color = new Color(look.color);
    material.opacity = look.opacity;
  }

  function draw() {
    renderer.render(scene, camera);
    stats.frames += 1;
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
      core.rotation.y += look.speed * dt;
      ring.rotation.z -= look.speed * 0.5 * dt;
      draw();
    }
    handle = requestAnimationFrame(loop);
  }

  function schedule() {
    if (!handle && stats.running && !reducedMotion) handle = requestAnimationFrame(loop);
  }

  canvas.addEventListener('webglcontextlost', (event) => {
    event.preventDefault();
    stats.contextLost = true;
    onContextLost();
  });
  canvas.addEventListener('webglcontextrestored', () => {
    stats.contextLost = false;
    apply();
    if (reducedMotion) draw(); else schedule();
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) schedule(); });

  apply();
  return {
    ok: true,
    setState(state) {
      stats.state = STATE_LOOKS[state] ? state : 'unknown'; // unrecognised -> unknown, never idle
      look = STATE_LOOKS[stats.state];
      apply();
      if (reducedMotion || !stats.running) draw();
    },
    resize(width, height, pixelRatio = 1) {
      renderer.setPixelRatio(Math.min(pixelRatio, 2));
      renderer.setSize(width, height, false);
      camera.aspect = width / Math.max(1, height);
      camera.updateProjectionMatrix();
      if (reducedMotion || !stats.running) draw();
    },
    start() {
      stats.running = true;
      if (reducedMotion) draw(); else schedule();
    },
    stop() { stats.running = false; if (handle) cancelAnimationFrame(handle); handle = 0; },
    dispose() {
      this.stop();
      core.geometry.dispose(); ring.geometry.dispose(); material.dispose(); renderer.dispose();
    },
    stats() {
      const info = renderer.info;
      return { ...stats, drawCalls: info.render.calls, triangles: info.render.triangles,
               geometries: info.memory.geometries };
    },
  };
}
