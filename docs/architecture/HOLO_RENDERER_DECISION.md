# Hologram renderer decision (MIA-HOLO-02)

Status: **decided for the spike; owner device verification pending.** Author: A6, 2026-11-22.
Scope: choose the browser 3D approach for the holographic interface (M2). No product UI uses it yet.

## What was measured

Same minimal scene (one wireframe/solid sphere, camera, render loop), tree-shaken and minified with
esbuild 0.28.2, then `gzip -9`. Versions are those npm served on 2026-11-22. Reproduce with the
scratch recipe in "Method" below.

| Library | Version | Licence | Minimal scene, raw | Minimal scene, gzip | Last npm publish |
|---|---|---|---:|---:|---|
| OGL | 1.0.11 | Unlicense | 48,409 B | 14,041 B | 2025-01-27 |
| three.js | 0.187.0 | MIT | 534,807 B | 133,157 B | 2026-10-30 |
| Babylon.js core (`@babylonjs/core`) | 9.37.1 | Apache-2.0 | 1,213,405 B | 302,092 B | not checked |

The spike itself (three.js, ten state looks, lifecycle handling) is a 538,825 B self-contained HTML
file, 134,843 B gzipped. It runs offline; Mia serves everything from the owner's machine, so these
sizes are a local disk/parse cost, not a network cost.

## Decision

Use **three.js** behind the small interface in `spikes/holo-renderer/src/renderer.js`.

Why: it is the actively maintained option (published within the last month), MIT-licensed, has no
runtime dependencies, tree-shakes, and has the widest ecosystem for the later work (picking,
post-processing, loaders). The size penalty over OGL (about 119 KB gzip) is small for a locally served
app. OGL is the credible alternative: 9.5x smaller, but its last publish is 10 months old and it is
far less used. Babylon.js is rejected for this use: about 2.3x three.js at the same scene.

Reverse the decision if: HOLO-06 profiling shows parse/start cost or memory matter on the owner's
hardware (switch to OGL; only `renderer.js` changes), or three.js drops tree-shaking/offline use.

## What the spike proves (real Chromium, software GL)

`tests/test_holo_renderer_spike.py` (8 tests):

- WebGL path renders and animates; draw calls are recorded.
- Missing WebGL returns `{ok:false, reason:"webgl_unavailable"}` and shows a text fallback
  (`role="status"`); nothing throws and chat/voice are unaffected.
- All ten HOLO-01 states have a look; an unrecognised state renders as `unknown`, never `idle`.
- `prefers-reduced-motion` draws static frames only (one redraw per state change).
- The loop pauses while the page is hidden and resumes; frame rate is capped (default 30 fps).
- WebGL context loss stops drawing and is reported; `dispose()` stops the loop and frees geometry.

Finding fixed during the spike: a `display:block` rule on the canvas overrode the `hidden`
attribute, so the fallback and canvas showed together. The page now has `[hidden]{display:none!important}`.

## Accessibility position

A WebGL canvas is not accessible content. The canvas must stay decorative; state is conveyed by a
text status (the HOLO-01 `statement`) in a live region, keyboard-reachable controls, and the
non-3D fallback. That work is MIA-HOLO-05; the spike only shows the fallback switch works.

## Not verified

- **Frame rate, GPU memory and power on real hardware**: the tests ran on software GL. Windows
  integrated/discrete GPUs are OWNER_VERIFICATION_REQUIRED and are MIA-HOLO-06's job.
- WebGL2 availability across the owner's browsers/drivers; the spike asks for the default context only.
- Screen-reader behaviour, contrast and narrow-screen layout (HOLO-05).
- No dependency-free raw-WebGL variant was built or measured; it would be smaller still but would
  mean maintaining matrix math, geometry and shader plumbing ourselves.
- Babylon.js maintenance date was not checked.

## Method

In a scratch directory: `npm i three ogl @babylonjs/core esbuild`; for each library a ~10-line entry
that creates a renderer, camera, one sphere mesh and a render loop (Babylon imports only the engine,
scene, camera, mesh builder and vector modules); `esbuild --bundle --minify --format=iife`; size with
`stat` and `gzip -9 -c | wc -c`. Licences from each package's `package.json`; publish dates from
`npm view <pkg> time.modified`. Numbers are for these specific minimal scenes, not whole applications.
