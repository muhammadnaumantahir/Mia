// Bundles src/main.js into one self-contained page the app serves at /hologram
// (no network at runtime). Output is committed: running Mia must not need Node.
import { build } from 'esbuild';
import { writeFileSync } from 'node:fs';

const out = new URL('../../src/mia/observatory/hologram.html', import.meta.url);
const result = await build({
  entryPoints: ['src/main.js'], bundle: true, minify: true, format: 'iife', write: false,
  legalComments: 'none', target: 'es2020',
});
const js = result.outputFiles[0].text.replaceAll('</script', '<\\/script');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mia hologram (preview)</title>
<style>
[hidden]{display:none!important}
:root{color-scheme:dark}
html,body{margin:0;min-height:100%;background:#05080d;color:#d8f4f8;font:16px/1.5 system-ui,sans-serif}
main{display:grid;gap:1rem;padding:1rem;max-width:56rem;margin:0 auto}
h1{font-size:1.25rem;margin:0}
#stage{width:100%;max-width:36rem;aspect-ratio:1;max-height:70vh;margin:0 auto;border:1px solid #1d3a44;border-radius:12px;display:grid;place-items:center;background:#070d14}
#scene{width:100%;height:100%;display:block}
#status{font-size:1.05rem;margin:0;min-height:3rem}
.note{color:#9db8c0;font-size:.9rem;margin:0}
label{display:block;margin-bottom:.25rem}
select{font:inherit;padding:.4rem;background:#0b141c;color:inherit;border:1px solid #2a4a56;border-radius:6px}
@media (min-width:60rem){main{grid-template-columns:2fr 1fr;align-items:start}h1{grid-column:1/-1}}
</style></head>
<body><main>
<h1>Mia hologram</h1>
<div id="stage"><canvas id="scene" aria-hidden="true"></canvas>
<p id="fallback" hidden>3D view unavailable on this device. Chat and voice are unaffected.</p></div>
<div>
<p id="status" role="status" aria-live="polite"></p>
<label for="preview">Preview a state</label><select id="preview"></select>
<p class="note">Preview only: this page is not connected to Mia's live activity yet, so the state shown is the one you pick, not what Mia is doing.</p>
</div>
</main>
<!-- three.js (MIT, https://github.com/mrdoob/three.js) is bundled below. Rebuild: cd frontend/holo && npm ci && npm run build -->
<script>${js}</script></body></html>
`;
writeFileSync(out, html);
console.log('hologram.html', html.length, 'bytes');
