// Execute the real hosted bootstrap with a mocked local API, without media/providers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let moduleId = 0;
const settle = () => new Promise((resolve) => setImmediate(resolve));

async function page(request) {
  const elements = Object.fromEntries(['workspace-content', 'workspace-loading', 'workspace-connection-alert', 'workspace-connection-status', 'workspace-retry']
    .map((id) => [id, { hidden: id !== 'workspace-loading', textContent: '', handlers: {}, addEventListener(name, fn) { this.handlers[name] = fn; } }]));
  let options, setups = 0, requests = 0;
  globalThis.workspaceClient = (callbacks) => {
    options = callbacks;
    return { request: async (...args) => { requests++; return request(...args); }, retain() {}, connected: () => false };
  };
  globalThis.setupConfig = async () => { setups++; };
  globalThis.showCalls = globalThis.showCall = async () => {};
  globalThis.document = { getElementById: (id) => elements[id], body: { dataset: { page: 'config' } }, querySelectorAll: () => [] };
  globalThis.location = { origin: 'https://voxlaneai.vercel.app', pathname: '/config', reload() { assert.fail('Retry must preserve current page/media controls'); } };
  const source = (await readFile(new URL('../workspace.js', import.meta.url), 'utf8')).replace(/^import .*;\r?\n/gm, '');
  await import(`data:text/javascript;base64,${Buffer.from(`${source}\n// case ${moduleId++}`).toString('base64')}`);
  await settle();
  return { elements, callbacks: options, requests: () => requests, setups: () => setups };
}

test('first navigation connects automatically without storage markers and hides connection UI on success', async () => {
  const view = await page(async (path) => { assert.equal(path, '/api/workspace/config'); return {}; });
  assert.equal(view.requests(), 1); assert.equal(view.setups(), 1);
  assert.equal(view.elements['workspace-content'].hidden, false);
  assert.equal(view.elements['workspace-loading'].hidden, true);
  assert.equal(view.elements['workspace-connection-alert'].hidden, true);
});

test('unreachable backend shows a compact retry and retries safely after a failed automatic attempt', async () => {
  let available = false;
  const view = await page(async () => { if (!available) throw new Error('Start FastAPI on this laptop. Allow Chrome local-network access.'); return {}; });
  assert.equal(view.elements['workspace-connection-alert'].hidden, false);
  assert.match(view.elements['workspace-connection-status'].textContent, /Start FastAPI/);
  assert.equal(view.elements['workspace-content'].hidden, true);
  available = true;
  view.elements['workspace-retry'].handlers.click(); await settle();
  assert.equal(view.requests(), 2); assert.equal(view.setups(), 1);
  assert.equal(view.elements['workspace-connection-alert'].hidden, true);
  assert.equal(view.elements['workspace-content'].hidden, false);
});

test('a dropped connection keeps loaded controls; retry avoids duplicate initialization and reloads', async () => {
  const view = await page(async () => ({}));
  view.callbacks.onConnectionLost(new Error('Local backend is unavailable.'));
  assert.equal(view.elements['workspace-connection-alert'].hidden, false);
  assert.equal(view.elements['workspace-content'].hidden, false);
  view.elements['workspace-retry'].handlers.click(); await settle();
  assert.equal(view.requests(), 2); assert.equal(view.setups(), 1);
  assert.equal(view.elements['workspace-connection-alert'].hidden, true);
});
