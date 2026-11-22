# Hologram frontend (MIA-HOLO-02 spike, MIA-HOLO-03 scene)

Sources for the page the app serves at `/hologram`. Running Mia does not need Node; the built page
`src/mia/observatory/hologram.html` is committed and a test fails if it is out of date.

    cd frontend/holo && npm ci && npm run build

- `src/renderer.js` is the only file that knows the 3D library. `createHoloRenderer(canvas, opts)`
  returns `{ok:false, reason}` when WebGL is unavailable, otherwise
  `setState/resize/start/stop/step/dispose/stats`. It takes a state name and draws; it never fetches.
- `poseAt(look, t)` is pure, so animation is testable without a GPU. `step(dt)` advances it by hand.
- States are the ten HOLO-01 names (`GET /api/runtime/contract`); an unrecognised name shows
  `unknown`, never `idle`. Colour is never the only cue: motion and core shape differ per state.
- `/hologram` is a preview: the state is picked by the owner (or `?state=`), not bound to live events
  (that is MIA-HOLO-04). The page says so on screen.
