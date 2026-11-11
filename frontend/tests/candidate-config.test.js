import test from 'node:test';
import assert from 'node:assert/strict';
import { candidateLocation, validateApiBase, fetchCandidate, sdkCandidateFetch } from '../candidate-config.js';

test('hosted direct links and refresh retain the scoped token without query parameters', () => {
  const id = 'a'.repeat(32); const values = new Map(); const rewrites = [];
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  const history = { replaceState: (...args) => rewrites.push(args[2]) };
  const root = () => ({ dataset: {} });
  const first = candidateLocation(root(), { pathname: `/candidate/${id}`, hash: '#token=private-token' }, storage, history, 'http://127.0.0.1:8000');
  assert.equal(first.sessionId, id); assert.equal(first.token, 'private-token');
  assert.equal(first.base, `http://127.0.0.1:8000/api/candidate/${id}`);
  const refreshed = candidateLocation(root(), { pathname: `/candidate/${id}`, hash: '' }, storage, history, 'http://127.0.0.1:8000');
  assert.equal(refreshed.token, first.token);
  assert.deepEqual(rewrites, [`/candidate/${id}`, `/candidate/${id}`]);
  const another = candidateLocation(root(), { pathname: `/candidate/${'b'.repeat(32)}`, hash: '' }, storage, history, 'http://127.0.0.1:8000');
  assert.equal(another.token, '');
});

test('local links keep same-origin APIs and the existing tab-storage key', () => {
  const values = new Map([['interview-token-local-id', 'existing-token']]);
  const context = candidateLocation({ dataset: { sessionId: 'local-id' } }, { pathname: '/candidate/local-id', hash: '' },
    { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) }, { replaceState() {} });
  assert.equal(context.base, '/api/candidate/local-id'); assert.equal(context.token, 'existing-token');
});

test('storage denial preserves the fragment for refresh and incomplete links do not invent an ID', () => {
  let cleaned = false;
  const location = { pathname: `/candidate/${'a'.repeat(32)}`, hash: '#token=private-token' };
  const context = candidateLocation({ dataset: {} }, location, { getItem() { throw Error(); }, setItem() { throw Error(); } },
    { replaceState() { cleaned = true; } }, 'http://127.0.0.1:8000');
  assert.equal(context.token, 'private-token'); assert.equal(cleaned, false);
  const incomplete = candidateLocation({ dataset: {} }, { pathname: '/', hash: '' }, { getItem() {} }, { replaceState() {} });
  assert.equal(incomplete.sessionId, '');
});

test('public backend configuration accepts only a loopback HTTP origin', () => {
  assert.equal(validateApiBase('http://127.0.0.1:8000/'), 'http://127.0.0.1:8000');
  for (const value of ['https://provider.example', 'http://127.0.0.1.evil.example:8000', 'http://user:secret@127.0.0.1:8000',
    'http://127.0.0.1:8000/api', 'http://127.0.0.1:8000/?token=secret']) assert.throws(() => validateApiBase(value));
});

test('network help is specific to hosted fetch failures; HTTP auth errors remain available to the caller', async () => {
  await assert.rejects(fetchCandidate('http://127.0.0.1:8000/api/candidate/id', {}, 'http://127.0.0.1:8000', () => { throw TypeError(); }), /Start FastAPI.*same laptop/);
  const response = await fetchCandidate('http://127.0.0.1:8000/api/candidate/id', {}, 'http://127.0.0.1:8000',
    async (_input, options) => { assert.equal(options.targetAddressSpace, 'loopback'); return Response.json({ detail: 'Invalid candidate link' }, { status: 403 }); });
  assert.equal(response.status, 403); assert.equal((await response.json()).detail, 'Invalid candidate link');
});

test('SDK final fetch restores loopback options lost through native Request cloning without changing other fetches', async () => {
  const original = new Request('http://127.0.0.1:8000/api/candidate/id/offer', { method: 'POST', headers: { Authorization: 'Bearer scoped-token', 'Content-Type': 'application/json' } });
  const cloned = new Request(new Request(original, { body: '{"sdp":"test"}' }), { signal: new AbortController().signal });
  let observed;
  const fetcher = async (input, init) => { observed = { input, init }; return Response.json({}); };
  await sdkCandidateFetch(cloned, undefined, 'http://127.0.0.1:8000', fetcher);
  assert.equal(observed.init.targetAddressSpace, 'loopback'); assert.equal(observed.init.referrerPolicy, 'no-referrer');
  assert.equal(observed.input.headers.get('Authorization'), 'Bearer scoped-token');
  await sdkCandidateFetch(original, { method: 'PATCH', body: '{"candidates":[]}' }, 'http://127.0.0.1:8000', fetcher);
  assert.equal(observed.init.targetAddressSpace, 'loopback'); assert.equal(observed.init.method, 'PATCH');
  const untouched = { method: 'GET' };
  await sdkCandidateFetch('/static/vision/model.tflite', untouched, 'http://127.0.0.1:8000', fetcher);
  assert.equal(observed.init, untouched);
});
