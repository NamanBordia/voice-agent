import { copyFile, cp, lstat, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { bundleBrowserEntry, verifyClientProtocol } from './build-common.mjs';
import { validateApiBase } from './candidate-config.js';
import { auditStaticOutput, candidateHtml, workspaceHtml } from './static-output.mjs';

const frontend = await realpath(fileURLToPath(new URL('.', import.meta.url)));
const directory = resolve(frontend, 'dist');
// Delete only the verified generated output; never follow a substituted directory link.
if (directory !== join(frontend, 'dist')) throw new Error('Invalid static output location.');
try { if ((await lstat(directory)).isSymbolicLink()) throw new Error('Static output cannot be a symlink.'); }
catch (error) { if (error.code !== 'ENOENT') throw error; }
const apiBase = validateApiBase(process.env.PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000');
await verifyClientProtocol();
await import('./prepare-vision.mjs'); // Download pinned models at setup only; no provider credentials.
await rm(directory, { recursive: true, force: true });
const output = new URL('./dist/static/', import.meta.url);
await mkdir(output, { recursive: true });
for (const entry of ['candidate', 'candidate-view', 'demo', 'recruiter', 'workspace', 'camera-worker']) await bundleBrowserEntry(entry, output, apiBase);
for (const name of ['base.css', 'studio.css', 'talk.css']) await copyFile(new URL(`../app/static/${name}`, import.meta.url), new URL(name, output));
await mkdir(new URL('vision/', output), { recursive: true });
for (const name of ['blaze_face_short_range.tflite', 'efficientdet_lite0.tflite']) await copyFile(new URL(`../app/static/vision/${name}`, import.meta.url), new URL(`vision/${name}`, output));
await cp(new URL('node_modules/@mediapipe/tasks-vision/wasm/', import.meta.url), new URL('vision/wasm/', output), { recursive: true });
const template = await readFile(new URL('../app/templates/candidate.html', import.meta.url), 'utf8');
const shell = await readFile(new URL('candidate-shell.html', import.meta.url), 'utf8');
await writeFile(new URL('dist/candidate.html', import.meta.url), candidateHtml(template, shell, apiBase));
const workspaceShell = await readFile(new URL('workspace-shell.html', import.meta.url), 'utf8');
for (const [page, source, target] of [['interviews', 'interviews', 'index'], ['talk', 'demo', 'talk'], ['config', 'config', 'config'], ['calls', null, 'calls'], ['call', null, 'call']]) {
  const sourceTemplate = source ? await readFile(new URL(`../app/templates/${source}.html`, import.meta.url), 'utf8') : '';
  await writeFile(new URL(`dist/${target}.html`, import.meta.url), workspaceHtml(sourceTemplate, workspaceShell, apiBase, page));
}
const files = await auditStaticOutput(directory);
console.log(`Static workspace and candidate frontend: ${files.length} public files in frontend/dist; API ${apiBase}. No backend code, credentials or interview records copied.`);
