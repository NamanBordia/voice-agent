import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

export function candidateHtml(template, shell, apiBase) {
  const content = template.match(/{% block content %}([\s\S]*?){% endblock %}/)?.[1];
  if (!content) throw new Error('Candidate template has no content block.');
  const body = content.replace('{{ session_id }}', '');
  if (/{[{%#]/.test(body)) throw new Error('New Jinja content needs explicit static rendering; refusing an incomplete shell.');
  const daily = 'https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net';
  const csp = `default-src 'self'; script-src 'self' 'wasm-unsafe-eval' blob: ${daily}; style-src 'self' 'unsafe-inline'; connect-src 'self' ${apiBase} blob: ${daily}; worker-src 'self' blob:; img-src 'self'; media-src 'self' blob:; frame-src 'self'; object-src 'none'; base-uri 'none'`;
  return shell.replace('<!-- CANDIDATE_CONTENT -->', body).replace('__CANDIDATE_CSP__', csp.replaceAll('"', '&quot;'));
}

export async function auditStaticOutput(directory) {
  const expected = new Set(['index.html', ...['candidate.js','candidate-view.js','demo.js','camera-worker.js','base.css','studio.css','talk.css'].map((name) => `static/${name}`),
    'static/vision/blaze_face_short_range.tflite','static/vision/efficientdet_lite0.tflite',
    ...['vision_wasm_internal.js','vision_wasm_internal.wasm','vision_wasm_nosimd_internal.js','vision_wasm_nosimd_internal.wasm'].map((name) => `static/vision/wasm/${name}`)]);
  const found = [];
  async function visit(path) {
    for (const entry of await readdir(path, { withFileTypes: true })) {
      const file = join(path, entry.name);
      if (entry.isSymbolicLink()) throw new Error('Static output must not contain symlinks.');
      if (entry.isDirectory()) await visit(file);
      else found.push(relative(directory, file).replaceAll('\\', '/'));
    }
  }
  await visit(directory);
  if (found.some((name) => !expected.has(name)) || [...expected].some((name) => !found.includes(name))) throw new Error('Static output contains unexpected or missing files; refusing deployment.');
  const html = await readFile(join(directory, 'index.html'), 'utf8');
  if (/{[{%#]/.test(html) || /__CANDIDATE_|<!-- CANDIDATE_CONTENT -->/.test(html)) throw new Error('Unrendered static template.');
  for (const match of html.matchAll(/(?:src|href)="(\/static\/[^"]+)"/g)) {
    if (!expected.has(match[1].slice(1))) throw new Error(`Broken static reference: ${match[1]}`);
  }
  return found;
}
