import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { candidateHtml, auditStaticOutput } from '../static-output.mjs';

test('static candidate shell reuses current UI and exposes no recruiter controls or Jinja', async () => {
  const template = await readFile(new URL('../../app/templates/candidate.html', import.meta.url), 'utf8');
  const shell = await readFile(new URL('../candidate-shell.html', import.meta.url), 'utf8');
  const html = candidateHtml(template, shell, 'http://127.0.0.1:8000');
  assert.ok(!/{[{%#]/.test(html)); assert.match(html, /data-session-id=""/);
  for (const id of ['camera-preview', 'bot-audio', 'start', 'mute', 'end', 'abandon', 'camera-consent', 'connect-backend']) assert.match(html, new RegExp(`id="${id}"`));
  assert.ok(!html.includes('href="/interviews"')); assert.ok(!html.includes('id="plan-editor"'));
  assert.match(html, /connect-src[^;]*http:\/\/127\.0\.0\.1:8000/);
  assert.ok(!html.includes('upgrade-insecure-requests'));
  assert.match(html, /name="referrer" content="no-referrer"/);
  assert.throws(() => candidateHtml(template.replace('id="camera-preview"', 'id="camera-preview" {{ new_server_data }}'), shell, 'http://127.0.0.1:8000'));
});

test('static output audit refuses private or unexpected files rather than publishing them', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'candidate-static-'));
  try { await writeFile(join(dir, '.env'), 'not a real secret'); await assert.rejects(auditStaticOutput(dir), /unexpected or missing/); }
  finally { await rm(dir, { recursive: true, force: true }); }
});
