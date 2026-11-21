# Hologram renderer spike (MIA-HOLO-02)

Isolated spike. Nothing under `src/mia` imports it. The decision and measurements are in
`docs/architecture/HOLO_RENDERER_DECISION.md`.

    npm ci && npm run build      # writes spike.html (generated, git-ignored, ~540 KB, offline)
    open spike.html?state=thinking

`src/renderer.js` is the only file that knows the 3D library. It exposes
`createHoloRenderer(canvas, {reducedMotion, maxFps, onContextLost})`, returning
`{ok:false, reason}` when WebGL is unavailable, otherwise `setState/resize/start/stop/dispose/stats`.
States are the ten HOLO-01 states (`GET /api/runtime/contract`); an unrecognised state renders as
`unknown`, never `idle`.
