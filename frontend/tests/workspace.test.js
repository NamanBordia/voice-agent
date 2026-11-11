import test from 'node:test';
import assert from 'node:assert/strict';
import { workspaceClient, workspaceStorageKey } from '../workspace-api.js';
import { sdkCandidateFetch } from '../candidate-config.js';

const apiBase = 'http://127.0.0.1:8000';
function tab() {
  const values = new Map();
  return { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

test('workspace connects without credentials and removes obsolete pairing data', async () => {
  const storage = tab(); const requests = [];
  storage.setItem('interview-token-candidate', 'candidate-token');
  storage.setItem(`workspace-pairing-${apiBase}`, 'obsolete-code');
  const client = workspaceClient({ apiBase, storage, fetcher: async (url, init) => {
    requests.push({ url, init }); return Response.json({ saved: true });
  } });
  assert.equal(storage.getItem(`workspace-pairing-${apiBase}`), undefined);
  assert.equal(storage.getItem(workspaceStorageKey(apiBase)), undefined);
  await client.request('/api/interviews', 'POST', { title: 'Synthetic' });
  client.retain();
  const saved = requests[0];
  assert.equal(saved.url, `${apiBase}/api/interviews`);
  assert.equal(saved.init.headers.Authorization, undefined);
  assert.equal(saved.init.targetAddressSpace, 'loopback');
  assert.equal(saved.init.referrerPolicy, 'no-referrer');
  assert.equal(saved.init.credentials, 'omit');
  assert.equal(storage.getItem(workspaceStorageKey(apiBase)), 'yes');
  assert.equal(client.connected(), true);
  for (const path of ['/api/candidate/session', '/api/workspace/pair', 'https://other.example/api/interviews', '/config']) {
    await assert.rejects(client.request(path), /outside/);
  }
  assert.equal(requests.length, 1);
  client.clear(); assert.equal(storage.getItem('interview-token-candidate'), 'candidate-token');
});

test('origin revocation clears the connection marker and preserves HTTP validation messages', async () => {
  const storage = tab(); storage.setItem(workspaceStorageKey(apiBase), 'yes'); let revoked = 0;
  const client = workspaceClient({ apiBase, storage, onAccessDenied: () => revoked++, fetcher: async () => Response.json({ detail: 'Origin access disabled' }, { status: 403 }) });
  await assert.rejects(client.request('/api/workspace/config'), /Origin access disabled/);
  assert.equal(revoked, 1); assert.equal(storage.getItem(workspaceStorageKey(apiBase)), undefined);
  const invalid = workspaceClient({ apiBase, storage, fetcher: async () => Response.json({ detail: 'Origin is not approved' }, { status: 403 }) });
  await assert.rejects(invalid.request('/api/workspace/config'), /Origin is not approved/);
  storage.setItem(workspaceStorageKey(apiBase), 'yes'); let lost = 0;
  const offline = workspaceClient({ apiBase, storage, onConnectionLost: () => lost++, fetcher: async () => { throw new TypeError('Failed to fetch'); } });
  await assert.rejects(offline.request('/api/interviews'), /Start FastAPI/);
  assert.equal(lost, 1); assert.equal(offline.connected(), false);
});

test('Talk exposes initial offer, end and exit requests without recruiter credentials', async () => {
  const seen = []; const client = workspaceClient({ apiBase, storage: tab(), fetcher: async (url, init) => { seen.push({ url, init }); return Response.json({ saved: true }); } });
  const voice = client.voice();
  assert.equal(voice.offerRequest.endpoint.url, `${apiBase}/api/offer`);
  assert.equal(voice.offerRequest.endpoint.headers.get('Authorization'), null);
  const sid = `browser_${'a'.repeat(32)}`;
  await voice.fetch(voice.endEndpoint(sid), { method: 'POST', keepalive: true });
  assert.equal(seen[0].url, `${apiBase}/api/sessions/${sid}/end`);
  assert.equal(seen[0].init.headers.Authorization, undefined);
  assert.equal(seen[0].init.keepalive, true);
  assert.equal(voice.beforeStart, undefined);
  await assert.rejects(async () => voice.fetch('https://other.example/api/offer'), /Invalid workspace/);
});

test('final SDK fetch restores loopback options for general Talk offers and PATCH ICE', async () => {
  for (const method of ['POST', 'PATCH']) {
    const request = new Request(`${apiBase}/api/offer`, { method });
    let captured;
    await sdkCandidateFetch(request, { method }, apiBase, async (input, init) => { captured = { input, init }; return Response.json({}); });
    assert.equal(captured.input.headers.get('Authorization'), null);
    assert.equal(captured.init.targetAddressSpace, 'loopback');
    assert.equal(captured.init.credentials, 'omit');
  }
});
