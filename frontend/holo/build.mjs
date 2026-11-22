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
<title>Mia hologram</title>
<style>
[hidden]{display:none!important}
:root{color-scheme:dark}
html,body{margin:0;min-height:100%;background:#05080d;color:#d8f4f8;font:16px/1.5 system-ui,sans-serif}
main{display:grid;gap:1rem;padding:1rem;max-width:56rem;margin:0 auto;grid-template-columns:minmax(0,1fr)}
main>*,fieldset{min-width:0}
h1,#status,.note{overflow-wrap:anywhere}
h1{font-size:1.25rem;margin:0}
#stage{width:100%;max-width:36rem;aspect-ratio:1;max-height:70vh;margin:0 auto;border:1px solid #1d3a44;border-radius:12px;display:grid;place-items:center;background:#070d14}
#scene{width:100%;height:100%;display:block}
#status{font-size:1.05rem;margin:0;min-height:3rem}
.note{color:#9db8c0;font-size:.9rem;margin:0}
label{display:block;margin-bottom:.25rem}
fieldset{border:1px solid #1d3a44;border-radius:8px;margin:0 0 .75rem;padding:.4rem .75rem}
legend{padding:0 .25rem;color:#9db8c0}
label.inline{display:inline-block;margin-right:1rem}
select:disabled{opacity:.5}
select{font:inherit;padding:.4rem;background:#0b141c;color:inherit;border:1px solid #2a4a56;border-radius:6px}
a:focus-visible,input:focus-visible,select:focus-visible,button:focus-visible{outline:3px solid #ffd54a;outline-offset:2px}
label.inline{min-height:2.75rem;line-height:2.75rem}
label.inline input{width:1.4rem;height:1.4rem;margin:0 .4rem 0 0;vertical-align:middle}
select{min-height:2.75rem;max-width:100%}
#glyphView{display:grid;justify-items:center;gap:.5rem;padding:1rem;text-align:center}
#glyph{width:7rem;height:7rem;color:#d8f4f8}
#glyph *{fill:none;stroke:currentColor;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
#glyphName{font-size:1.4rem;font-weight:600;margin:0}
.skip{position:absolute;left:-999px}.skip:focus{left:1rem;top:1rem;background:#ffd54a;color:#05080d;padding:.5rem;z-index:2}
@media (forced-colors:active){#stage,fieldset,select{border-color:CanvasText}#glyph *{stroke:CanvasText}}
@media (min-width:60rem){main{grid-template-columns:minmax(0,2fr) minmax(0,1fr);align-items:start}h1{grid-column:1/-1}}
</style></head>
<body><a class="skip" href="#status">Skip to current status</a><main>
<h1>Mia hologram</h1>
<div id="stage"><canvas id="scene" aria-hidden="true"></canvas>
<div id="glyphView" hidden><svg id="glyph" viewBox="0 0 100 100" aria-hidden="true" focusable="false"></svg>
<p id="glyphName"></p></div>
<p id="fallback" hidden>3D view unavailable on this device. The text view below shows the same states. Chat and voice are unaffected.</p></div>
<div>
<p id="status" role="status" aria-live="polite"></p>
<p id="evidence" class="note" aria-live="off"></p>
<p id="perfNote" class="note" hidden></p>
<fieldset><legend>View</legend>
<label class="inline"><input type="radio" name="view" value="3d"> 3D view</label>
<label class="inline"><input type="radio" name="view" value="text"> Text and shape view</label></fieldset>
<fieldset><legend>Show</legend>
<label class="inline"><input type="radio" name="mode" value="live"> Live activity</label>
<label class="inline"><input type="radio" name="mode" value="preview"> Preview a state</label></fieldset>
<label for="preview">Preview a state</label><select id="preview"></select>
<p id="previewNote" class="note" hidden>Preview only: the state shown is the one you pick, not what Mia is doing. Switch to Live activity to see real activity.</p>
</div>
</main>
<!-- three.js (MIT, https://github.com/mrdoob/three.js) is bundled below. Rebuild: cd frontend/holo && npm ci && npm run build -->
<script>${js}</script></body></html>
`;
writeFileSync(out, html);
console.log('hologram.html', html.length, 'bytes');
