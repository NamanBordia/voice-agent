// Use the installed transport's actual negotiation/ICE serialization. Devices,
// HTTP I/O, and SDP generation are mocked; no provider or microphone is used.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createCandidateHeaders } from '../candidate-http.js';
import { candidateTransport } from '../candidate-transport.js';

// The SDK's ESM entry contains an extensionless lodash import; its CJS entry
// exposes the same transport methods and can be loaded directly by Node.
const { SmallWebRTCTransport } = createRequire(import.meta.url)('@pipecat-ai/small-webrtc-transport');

test('candidate SDK POST and PATCH retain bearer auth and one JSON Content-Type', async () => {
  const { signalingHeaders, jsonHeaders } = createCandidateHeaders('test-participation-token');
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, options) => {
    const request = input instanceof Request ? input : new Request(input, options);
    requests.push({ method: request.method, headers: request.headers, body: await request.json() });
    return Response.json({ pc_id: 'test-peer', type: 'answer', sdp: 'test-answer' });
  };
  const CandidateTransport = candidateTransport(SmallWebRTCTransport);
  const transport = Object.create(CandidateTransport.prototype);
  transport._webrtcRequest = { endpoint: 'http://localhost/api/candidate/session/offer', headers: signalingHeaders };
  transport.pc_id = null;
  transport.pc = {
    localDescription: null,
    async createOffer() { return { type: 'offer', sdp: 'test-offer' }; },
    async setLocalDescription(offer) { this.localDescription = offer; },
    async setRemoteDescription(answer) { this.remoteDescription = answer; },
  };
  try {
    transport._canSendIceCandidates = true;
    transport._candidateQueue = [{ candidate: 'candidate:test', sdpMid: '0', sdpMLineIndex: 0 }];
    await transport.flushIceCandidates();
    assert.equal(requests.length, 0); // A null peer ID must never reach PATCH validation.
    assert.equal(transport._candidateQueue.length, 1);
    await transport.negotiate();
    await transport.flushIceCandidates();
    assert.deepEqual(requests.map((request) => request.method), ['POST', 'PATCH']);
    for (const request of requests) {
      assert.equal(request.headers.get('Content-Type'), 'application/json');
      assert.equal(request.headers.get('Authorization'), 'Bearer test-participation-token');
    }
    assert.equal(requests[0].body.sdp, 'test-offer');
    assert.equal(requests[1].body.pc_id, 'test-peer');
    assert.equal(jsonHeaders.get('Content-Type'), 'application/json');
    assert.equal(jsonHeaders.get('Authorization'), 'Bearer test-participation-token');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('candidate recovery reports one error and never automatically resends an offer', async () => {
  const CandidateTransport = candidateTransport(SmallWebRTCTransport);
  const transport = Object.create(CandidateTransport.prototype);
  const errors = [];
  transport._callbacks = { onError: (error) => errors.push(error) };
  transport.startNewPeerConnection = () => assert.fail('Resume must create the next peer');
  transport.negotiate = () => assert.fail('Do not renegotiate a reserved candidate session');
  await transport.attemptReconnection(true);
  await transport.attemptReconnection(true);
  assert.equal(errors.length, 1); assert.match(errors[0].message, /Resume interview/);
  transport.pc = null;
  await transport.stop(); // Actual SDK stop path when the peer is already gone.
  await transport.attemptReconnection(true);
  assert.equal(errors.length, 1);
});

test('an actual SDK offer rejection cannot cascade into null-peer PATCH or repeated POST', async (t) => {
  const CandidateTransport = candidateTransport(SmallWebRTCTransport);
  const transport = Object.create(CandidateTransport.prototype);
  const requests = []; const errors = []; let retry;
  transport._callbacks = { onError: (error) => errors.push(error) };
  transport._webrtcRequest = { endpoint: 'http://localhost/api/candidate/session/offer',
    headers: createCandidateHeaders('test-token').signalingHeaders };
  transport.pc_id = null;
  transport.pc = {
    async createOffer() { return { type: 'offer', sdp: 'test-offer' }; },
    async setLocalDescription(offer) { this.localDescription = offer; },
  };
  transport._canSendIceCandidates = true;
  transport._candidateQueue = [{ candidate: 'candidate:test' }];
  t.mock.method(globalThis, 'fetch', async (input, options) => {
    const request = input instanceof Request ? input : new Request(input, options);
    requests.push(request.method);
    return Response.json({ detail: 'Invalid signaling request' }, { status: 422 });
  });
  t.mock.method(globalThis, 'setTimeout', (callback, delay) => {
    assert.equal(delay, 2000); retry = callback; return 0;
  });
  await transport.negotiate();
  await transport.flushIceCandidates();
  assert.equal(typeof retry, 'function');
  await retry(); await retry();
  assert.deepEqual(requests, ['POST']);
  assert.equal(errors.length, 1);
});

test('a queued retry after candidate stop cannot create a peer or send ICE', async () => {
  const CandidateTransport = candidateTransport(SmallWebRTCTransport);
  const transport = Object.create(CandidateTransport.prototype);
  transport.pc = null;
  transport._callbacks = { onError: () => assert.fail('Stopped session cannot emit a new error') };
  transport.startNewPeerConnection = () => assert.fail('Stopped session cannot create a peer');
  await transport.stop();
  await transport.attemptReconnection(true);
  transport.pc_id = 'stale-peer';
  transport._canSendIceCandidates = true;
  transport._candidateQueue = [{ candidate: 'stale-candidate' }];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = () => assert.fail('Stopped session cannot PATCH ICE');
  try { await transport.flushIceCandidates(); }
  finally { globalThis.fetch = originalFetch; }
});
