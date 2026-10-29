// Actual candidate module with device/provider boundaries mocked.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createCandidateHeaders } from '../candidate-http.js';

let moduleId = 0;
class Element {
  handlers = {}; checked = false; disabled = false; textContent = ''; srcObject = null;
  dataset = { sessionId: 'candidate-session' };
  addEventListener(name, handler) { this.handlers[name] = handler; }
  async play() {}
}
function stream(kind) {
  const track = { kind, readyState: 'live', stop() { this.readyState = 'ended'; } };
  return { track, getTracks: () => [track], getVideoTracks: () => kind === 'video' ? [track] : [] };
}
async function page({ consent = true, acquire = async () => stream('video'), eventDelivery = true } = {}) {
  const elements = {}; const el = (id) => elements[id] ||= new Element();
  el('camera-consent').checked = consent; el('speaker-confirmed').checked = true;
  globalThis.document = { getElementById: el };
  globalThis.location = { pathname: '/candidate/candidate-session', hash: '#token=test-token' };
  globalThis.history = { replaceState() {} };
  globalThis.sessionStorage = { getItem() { return null; }, setItem() {} };
  const cameras = []; const monitors = []; const requests = []; let videoRequests = 0;
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: {
    async getUserMedia(constraints) {
      if (!constraints.video) return stream('audio');
      videoRequests += 1;
      const value = await acquire(videoRequests); cameras.push(value); return value;
    },
  } } });
  globalThis.AudioContext = class {
    async resume() {} async close() {}
    createAnalyser() { return { fftSize: 256, getByteTimeDomainData() {} }; }
    createMediaStreamSource() { return { connect() {} }; }
  };
  const server = { status: 'ready', elapsed_secs: 0, call_sid: null, monitoring_run: null,
    camera: { phone_enabled: true }, camera_sequence: 0, heartbeat_sequence: 0, title: 'Intern', language: 'en',
    duration_minutes: 5, remaining_secs: 300 };
  globalThis.fetch = async (path, options) => {
    const body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ path, method: options.method, body });
    if (path.endsWith('/prepare')) {
      if (server.status !== 'paused') server.status = body.phase;
      return Response.json({ status: server.status });
    }
    if (path.endsWith('/camera') && !eventDelivery) return Response.json({ detail: 'Event delivery failed' }, { status: 503 });
    if (path.endsWith('/camera') || path.endsWith('/heartbeat') || path.endsWith('/revoke-camera-consent')) return Response.json({ saved: true });
    return Response.json(server);
  };
  globalThis.candidateDeps = {
    createCandidateHeaders, SmallWebRTCTransport: class {}, candidateTransport: (Base) => Base,
    Daily: { getCallInstance: () => ({ async load() {} }) },
    CameraMonitor: class {
      constructor(options) { this.options = options; monitors.push(this); }
      async start(camera, consent) { this.camera = camera; this.consent = consent; }
      stop() { this.stopped = true; this.camera?.getTracks().forEach((track) => track.stop()); return Promise.resolve(); }
    },
  };
  const source = (await readFile(new URL('../candidate.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '')
    .replace("await import('./demo.js');", 'void 0;');
  const prefix = 'const { CameraMonitor, Daily, createCandidateHeaders, SmallWebRTCTransport, candidateTransport } = globalThis.candidateDeps;\n';
  await import(`data:text/javascript;base64,${Buffer.from(`${prefix}${source}\n// case ${moduleId++}`).toString('base64')}`);
  await el('check-mic').handlers.click();
  await el('ready-interview').handlers.click();
  const participation = globalThis.interviewParticipation;
  const announce = async (callSid) => {
    server.status = 'in_progress'; server.call_sid = callSid;
    server.monitoring_run = { call_sid: callSid };
    await participation.onSession(callSid);
  };
  const pause = async () => {
    await participation.beforeEnd(); server.status = 'paused'; server.elapsed_secs += 10;
    await participation.onEnd();
  };
  return { elements, cameras, monitors, requests, server, participation, announce, pause,
    videoRequests: () => videoRequests };
}

test('resume reacquires a fresh camera before monitoring the new call without another preview click', async () => {
  const p = await page();
  await p.elements['preview-camera'].handlers.click();
  assert.equal(p.videoRequests(), 1);
  assert.equal(await p.participation.beforeStart(), true);
  assert.equal(p.videoRequests(), 1); // Keep an existing live preview.
  await p.announce('call1');
  assert.equal(p.monitors[0].camera, p.cameras[0]);
  await p.pause();
  assert.equal(p.cameras[0].track.readyState, 'ended');
  assert.equal(p.elements['camera-preview'].srcObject, null);
  assert.equal(await p.participation.beforeStart(), true);
  assert.equal(p.videoRequests(), 2);
  await p.announce('call2');
  assert.equal(p.monitors[1].camera, p.cameras[1]);
  assert.equal(p.monitors[1].camera.track.readyState, 'live');
  assert.equal(p.monitors[1].options.callSid, 'call2');
  assert.equal(p.monitors[0].stopped, true);
  await p.pause();
});

test('candidate event queue returns a verified receipt and preserves identity across failed retries', async () => {
  for (const eventDelivery of [true, false]) {
    const p = await page({ eventDelivery });
    await p.participation.beforeStart(); await p.announce('call1');
    const event = { type: 'phone_detected', start_secs: 0, duration_secs: 2, confidence: .9, face_count: null };
    const receipt = await p.monitors[0].options.send(event);
    assert.equal(receipt.delivered, eventDelivery);
    const sends = p.requests.filter((request) => request.path.endsWith('/camera'));
    assert.equal(sends.length, eventDelivery ? 1 : 3);
    assert.equal(new Set(sends.map((request) => request.body.event_id)).size, 1);
    assert.equal(new Set(sends.map((request) => request.body.sequence)).size, 1);
    await p.pause();
  }
});

test('declined or withdrawn consent never reopens the camera on resume', async () => {
  const p = await page();
  await p.participation.beforeStart(); await p.announce('call1'); await p.pause();
  p.elements['camera-consent'].checked = false;
  await p.elements['camera-consent'].handlers.change();
  await p.elements['ready-interview'].handlers.click();
  assert.equal(await p.participation.beforeStart(), true);
  assert.equal(p.videoRequests(), 1);
  await p.announce('call2');
  assert.equal(p.monitors[1].camera, null); assert.equal(p.monitors[1].consent, false);
  await p.pause();
  const declined = await page({ consent: false });
  assert.equal(await declined.participation.beforeStart(), true);
  assert.equal(declined.videoRequests(), 0);
  await declined.participation.onEnd();
});

test('camera denial on resume leaves voice available and publishes no success claim', async () => {
  const p = await page({ acquire: async (attempt) => {
    if (attempt > 1) throw new Error('Permission denied');
    return stream('video');
  } });
  await p.participation.beforeStart(); await p.announce('call1'); await p.pause();
  assert.equal(await p.participation.beforeStart(), true);
  assert.match(p.elements['monitoring-status'].textContent, /Permission denied.*Voice can continue.*unknown/);
  await p.announce('call2');
  assert.equal(p.monitors[1].camera, null);
  await p.pause();
});

test('a camera permission request resolved after page exit cannot reopen the preview or start voice', async () => {
  let resolve;
  const p = await page({ acquire: () => new Promise((done) => { resolve = done; }) });
  const starting = p.participation.beforeStart();
  while (!resolve) await new Promise((done) => setImmediate(done));
  p.participation.onPageHide();
  const late = stream('video'); resolve(late);
  assert.equal(await starting, false);
  assert.equal(late.track.readyState, 'ended');
  assert.equal(p.elements['camera-preview'].srcObject, null);
  assert.equal(p.monitors.length, 0);
});
