// Bundles src/main.js into one self-contained spike.html (no network at runtime).
import { build } from 'esbuild';
import { writeFileSync } from 'node:fs';

const result = await build({
  entryPoints: ['src/main.js'], bundle: true, minify: true, format: 'iife', write: false,
  legalComments: 'none', target: 'es2020',
});
const js = result.outputFiles[0].text.replaceAll('</script', '<\\/script');
const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mia hologram renderer spike (MIA-HOLO-02)</title>
<style>[hidden]{display:none!important}html,body{margin:0;height:100%;background:#05080d;color:#cfe}#scene{width:100%;height:100%;display:block}
#fallback{padding:1rem;font:16px system-ui}</style></head>
<body><canvas id="scene"></canvas>
<p id="fallback" hidden role="status">3D view unavailable on this device. Chat and voice are unaffected.</p>
<!-- three.js (MIT, https://github.com/mrdoob/three.js) is bundled below. Rebuild with: npm install && npm run build -->
<script>${js}</script></body></html>
`;
writeFileSync('spike.html', html);
console.log('spike.html', html.length, 'bytes');
