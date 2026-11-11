import { readdir, readFile } from 'node:fs/promises';
import { join, relative } from 'node:path';

export function candidateHtml(template, shell, apiBase) {
  const content = template.match(/{% block content %}([\s\S]*?){% endblock %}/)?.[1];
  if (!content) throw new Error('Candidate template has no content block.');
  const body = content.replace('{{ session_id }}', '');
  if (/{[{%#]/.test(body)) throw new Error('New Jinja content needs explicit static rendering; refusing an incomplete shell.');
  return withCsp(shell.replace('<!-- CANDIDATE_CONTENT -->', body), apiBase);
}

function withCsp(html, apiBase) {
  const daily = 'https://*.daily.co https://*.dailywebrtc.com https://*.dailywebrtc.net';
  const csp = `default-src 'self'; script-src 'self' 'wasm-unsafe-eval' blob: ${daily}; style-src 'self' 'unsafe-inline'; connect-src 'self' ${apiBase} blob: ${daily}; worker-src 'self' blob:; img-src 'self'; media-src 'self' blob:; frame-src 'self'; object-src 'none'; base-uri 'none'`;
  return html.replace('__CANDIDATE_CSP__', csp.replaceAll('"', '&quot;'));
}

export function workspaceHtml(template, shell, apiBase, page) {
  let content = template?.match(/{% block content %}([\s\S]*?){% endblock %}/)?.[1] || '';
  if (['interviews', 'talk', 'config'].includes(page) && !content) throw new Error('Missing workspace template content.');
  if (page === 'config') {
    content = content.replace(/{% if (?:saved|error) %}[\s\S]*?{% endif %}/g, '')
      .replace(/\s+hx-[\w-]+="[^"]*"/g, '');
    content = content.replace(/<select([^>]*)>([\s\S]*?)<\/select>/g, (_, attrs, body) => {
      const field = body.match(/config\.(\w+\.\w+)/)?.[1];
      if (!field) throw new Error('Settings select needs a config field.');
      body = body.replace(/{% for (\w+) in (\[[\s\S]*?\]) %}([\s\S]*?){% endfor %}/g, (_, variable, list, option) => {
        const values = JSON.parse(list.replaceAll("'", '"'));
        return values.map((value) => option.replace(/{{\s*'selected'[^}]*}}/g, '')
          .replaceAll(`{{ ${variable} }}`, value)).join('');
      });
      return `<select${attrs} data-config-field="${field}">${body}</select>`;
    });
    content = content.replace(/value="{{ config\.(\w+\.\w+) }}"/g, 'data-config-field="$1" value=""')
      .replace(/(<textarea[^>]*)(>){{ config\.(\w+\.\w+) }}(<\/textarea>)/g, '$1 data-config-field="$3"$2$4')
      .replace('action="/config"', 'action="/config" data-hosted-config="true"');
  }
  if (/{[{%#]/.test(content)) throw new Error('Workspace shell contains unhandled server template expressions.');
  return withCsp(shell.replace('__WORKSPACE_PAGE__', page).replace('<!-- WORKSPACE_CONTENT -->', content), apiBase);
}

export async function auditStaticOutput(directory) {
  const expected = new Set(['index.html', 'candidate.html', 'talk.html', 'config.html', 'calls.html', 'call.html', ...['candidate.js','candidate-view.js','demo.js','recruiter.js','workspace.js','camera-worker.js','base.css','studio.css','talk.css'].map((name) => `static/${name}`),
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
  for (const name of [...expected].filter((name) => name.endsWith('.html'))) {
    const html = await readFile(join(directory, name), 'utf8');
    if (/{[{%#]/.test(html) || /__(?:CANDIDATE|WORKSPACE)_|<!-- (?:CANDIDATE|WORKSPACE)_CONTENT -->/.test(html)) throw new Error('Unrendered static template.');
    for (const match of html.matchAll(/(?:src|href)="(\/static\/[^"]+)"/g)) {
      if (!expected.has(match[1].slice(1))) throw new Error(`Broken static reference: ${match[1]}`);
    }
  }
  return found;
}
