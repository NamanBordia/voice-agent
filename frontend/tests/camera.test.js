import test from 'node:test';
import assert from 'node:assert/strict';
import { ObservationRules } from '../camera-rules.js';
import { CameraMonitor } from '../camera-monitor.js';
const config = { fps: 4, confidence: .6, absent_secs: 3, multiple_secs: 2, displacement_secs: 3, displacement_ratio: .25, cooldown_secs: 15 };
const observe = (count, center = { x: .5, y: .5 }, confidence = .9) => ({ count, center, confidence });
function sustained(rules, observation, start, duration) {
  const events = [];
  for (let t = start; t <= start + duration; t += .25) events.push(...rules.update(observation, t));
  return events;
}
test('absence and multiple-face observations require sustained intervals and cooldowns', () => {
  const rules = new ObservationRules(config);
  assert.equal(sustained(rules, observe(0), 0, 2.75).length, 0);
  assert.equal(rules.update(observe(0), 3)[0].type, 'face_absent');
  assert.equal(sustained(rules, observe(0), 3.25, 5).length, 0);
  assert.equal(sustained(rules, observe(2), 9, 2)[0].type, 'multiple_faces_detected');
});
test('low-confidence multiple detections and missed inference gaps do not create sustained events', () => {
  const rules = new ObservationRules(config);
  assert.equal(sustained(rules, observe(2, null, .3), 0, 4).length, 0);
  rules.update(observe(0), 5);
  assert.equal(rules.update(observe(0), 20).length, 0);
  assert.equal(sustained(rules, observe(0), 20.25, 2.5).length, 0);
});
test('position change compares a stable reference, then resets after an observation', () => {
  const rules = new ObservationRules(config);
  rules.update(observe(1), 0);
  assert.equal(sustained(rules, observe(1, { x: .9, y: .5 }), .25, 3)[0].type, 'face_position_changed');
  assert.equal(sustained(rules, observe(1, { x: .9, y: .5 }), 3.5, 4).length, 0);
});
function setup(bitmapFactory, extraConfig = {}) {
  globalThis.Worker = class {}; globalThis.OffscreenCanvas = class {}; globalThis.createImageBitmap = () => {};
  const messages = []; const events = []; const heartbeats = []; const snapshots = []; const statuses = [];
  let stopped = 0; let terminated = 0;
  const track = { stop() { stopped += 1; }, addEventListener(name, handler) { this[name] = handler; } };
  const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
  const video = { readyState: 2, srcObject: stream };
  const worker = { postMessage(data) { messages.push(data); }, terminate() { terminated += 1; } };
  const monitor = new CameraMonitor({ video, config: { ...config, ...extraConfig }, workerFactory: () => worker,
    bitmapFactory: bitmapFactory || (async () => ({ close() {}, width: 640, height: 360 })),
    send: (event) => events.push(event), heartbeat: (status, run) => { snapshots.push(run); heartbeats.push(status); }, status: (value) => statuses.push(value) });
  return { monitor, worker, stream, track, video, messages, events, heartbeats, snapshots, statuses, stopped: () => stopped, terminated: () => terminated };
}
test('one frame in flight drops busy work; stop terminates worker, releases tracks, clears preview', async () => {
  const s = setup();
  await s.monitor.start(s.stream, true); s.worker.onmessage({ data: { type: 'ready' } });
  await s.monitor.tick(); await s.monitor.tick();
  assert.equal(s.messages.filter((m) => m.type === 'frame').length, 1);
  s.worker.onmessage({ data: { type: 'result', count: 1, confidence: .9, center: { x: .5, y: .5 } } });
  await s.monitor.tick(); assert.equal(s.messages.filter((m) => m.type === 'frame').length, 2);
  s.monitor.stop(); s.monitor.stop();
  assert.equal(s.stopped(), 1); assert.equal(s.terminated(), 1); assert.equal(s.video.srcObject, null);
  assert.equal(s.heartbeats.at(-1), 'stopped');
});
test('late bitmap creation closes its frame after cleanup without posting to a dead worker', async () => {
  let resolve; let closed = false;
  const s = setup(() => new Promise((done) => { resolve = done; }));
  await s.monitor.start(s.stream, true); s.worker.onmessage({ data: { type: 'ready' } });
  const ticking = s.monitor.tick(); s.monitor.stop(); resolve({ close() { closed = true; } }); await ticking;
  assert.equal(closed, true); assert.equal(s.messages.filter((m) => m.type === 'frame').length, 0);
});
test('worker failure and ended tracks yield unavailable status, not a clean monitoring timeline', async () => {
  for (const cause of ['worker', 'track']) {
    const s = setup(); await s.monitor.start(s.stream, true);
    if (cause === 'worker') s.worker.onerror(); else s.track.ended();
    assert.equal(s.heartbeats.at(-1), 'unavailable');
    assert.equal(s.events[0].type, cause === 'worker' ? 'monitoring_unavailable' : 'camera_unavailable');
    assert.equal(s.terminated(), 1); assert.equal(s.stopped(), 1);
  }
});
test('declining consent never constructs or submits an analysis frame', async () => {
  const s = setup(); await s.monitor.start(s.stream, false);
  assert.equal(s.messages.length, 0); assert.equal(s.events[0].type, 'monitoring_unavailable');
});

test('a loaded detector reports active only after a valid analyzed frame', async () => {
  const s = setup(); await s.monitor.start(s.stream, true);
  assert.deepEqual(s.heartbeats, ['not_started']);
  s.worker.onmessage({ data: { type: 'ready' } });
  assert.equal(s.heartbeats.includes('active'), false);
  await s.monitor.tick();
  s.worker.onmessage({ data: { type: 'result', count: 1, confidence: .9, center: { x: .5, y: .5 } } });
  assert.deepEqual(s.heartbeats, ['not_started', 'active']);
  s.monitor.stop();
});

test('camera frames that never become available yield an unavailable observation', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const s = setup(); s.video.readyState = 1;
  await s.monitor.start(s.stream, true); s.worker.onmessage({ data: { type: 'ready' } });
  t.mock.timers.tick(5001);
  assert.equal(s.heartbeats.includes('active'), false);
  assert.equal(s.heartbeats.at(-1), 'unavailable');
  assert.equal(s.events[0].type, 'monitoring_unavailable');
  assert.equal(s.monitor.closed, true);
});

test('successful inferences cancel prior deadlines instead of failing later', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  const s = setup(); await s.monitor.start(s.stream, true);
  s.worker.onmessage({ data: { type: 'ready' } });
  clearInterval(s.monitor.timer);
  for (let i = 0; i < 7; i += 1) {
    t.mock.timers.tick(1000); await s.monitor.tick();
    s.worker.onmessage({ data: { type: 'result', count: 0, confidence: null, center: null } });
    assert.equal(s.monitor.closed, false);
  }
  assert.equal(s.heartbeats.includes('unavailable'), false);
  s.monitor.stop();
});

test('stalled bitmap creation and malformed results do not claim active monitoring', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout', 'setInterval'] });
  let resolve; let bitmapClosed = false;
  const s = setup(() => new Promise((done) => { resolve = done; }));
  await s.monitor.start(s.stream, true); s.worker.onmessage({ data: { type: 'ready' } });
  const tick = s.monitor.tick(); t.mock.timers.tick(5001);
  resolve({ close() { bitmapClosed = true; } }); await tick;
  assert.equal(bitmapClosed, true); assert.equal(s.heartbeats.at(-1), 'unavailable');
  assert.equal(s.messages.filter((message) => message.type === 'frame').length, 0);
  const other = setup(); await other.monitor.start(other.stream, true);
  other.worker.onmessage({ data: { type: 'ready' } }); await other.monitor.tick();
  other.worker.onmessage({ data: { type: 'result', count: 1, confidence: NaN } });
  assert.equal(other.heartbeats.includes('active'), false);
  assert.equal(other.heartbeats.at(-1), 'unavailable');
});

test('phone failure keeps face frames and publishes distinct final detector metrics', async () => {
  const s = setup(undefined, { phone_enabled: true });
  await s.monitor.start(s.stream, true);
  s.worker.onmessage({ data: { type: 'ready', detectors: { face: 'ready', phone: 'unavailable' }, errors: { phone: 'model_load_failed' } } });
  await s.monitor.tick();
  s.worker.onmessage({ data: { type: 'result', face: { count: 0, confidence: null, center: null }, phone: null, errors: {} } });
  assert.equal(s.monitor.closed, false);
  assert.equal(s.snapshots.at(-1).face.frames_analyzed, 1);
  assert.equal(s.snapshots.at(-1).phone.frames_analyzed, 0);
  assert.equal(s.snapshots.at(-1).phone.last_error, 'model_load_failed');
  await s.monitor.stop();
  assert.equal(s.snapshots.at(-1).face.status, 'stopped');
  assert.equal(s.snapshots.at(-1).phone.status, 'unavailable');
  assert.notEqual(s.snapshots.at(-1).end_secs, null);
});

test('sustained phone results reach event delivery even when face analysis is unavailable', async () => {
  const s = setup(undefined, { phone_enabled: true });
  let seconds = 0; s.monitor.seconds = () => seconds;
  await s.monitor.start(s.stream, true);
  s.worker.onmessage({ data: { type: 'ready', detectors: { face: 'unavailable', phone: 'ready' } } });
  for (seconds = 0; seconds <= 2; seconds += .5) {
    await s.monitor.tick();
    s.worker.onmessage({ data: { type: 'result', face: null, phone: { count: 1, confidence: .9 }, errors: {} } });
  }
  assert.equal(s.heartbeats.includes('active'), true);
  assert.equal(s.events.length, 1);
  assert.equal(s.events[0].type, 'phone_detected');
  assert.equal(s.events[0].confidence, .9);
  assert.equal(s.events[0].duration_secs, 2);
  assert.equal(s.monitor.phoneVisible, true);
  await s.monitor.stop();
  assert.equal(s.snapshots.at(-1).phone.frames_analyzed, 5);
  assert.equal(s.snapshots.at(-1).face.frames_analyzed, 0);
});

test('phone preview shows low scores, strongest candidate and rule resets without uploading diagnostic values', async () => {
  const s = setup(undefined, { phone_enabled: true });
  let seconds = 0; s.monitor.seconds = () => seconds;
  await s.monitor.start(s.stream, true);
  s.worker.onmessage({ data: { type: 'ready', detectors: { face: 'ready', phone: 'ready' } } });
  const infer = async (count, confidence) => {
    await s.monitor.tick();
    s.worker.onmessage({ data: { type: 'result', face: observe(1), phone: { count, confidence }, errors: {} } });
  };
  await infer(1, .45);
  assert.match(s.statuses.at(-1), /Phone score: 0.45; best: 0.45; event threshold: 0.70/);
  assert.match(s.statuses.at(-1), /score below event threshold/);
  assert.equal(s.events.length, 0);
  seconds = .5; await infer(1, .9);
  assert.match(s.statuses.at(-1), /Possible phone visible; sighting span 0.0\/2s/);
  seconds = 1; await infer(0, null);
  assert.match(s.statuses.at(-1), /Phone score: none; best: 0.90/);
  assert.match(s.statuses.at(-1), /One missed phone sample/);
  assert.equal(s.events.length, 0);
  await s.monitor.stop();
  assert.doesNotMatch(JSON.stringify(s.snapshots), /bestPhone|lastPhone|resetReason|eventsCreated|0\.45/);
});

test('saved and failed phone-event delivery remain visible on subsequent frames', async () => {
  for (const delivered of [true, false]) {
    const s = setup(undefined, { phone_enabled: true, phone_confidence: .3 });
    let seconds = 0; s.monitor.seconds = () => seconds;
    s.monitor.send = async (event) => { s.events.push(event); return { delivered }; };
    await s.monitor.start(s.stream, true);
    s.worker.onmessage({ data: { type: 'ready', detectors: { face: 'ready', phone: 'ready' } } });
    for (seconds = 0; seconds <= 3; seconds += .5) {
      await s.monitor.tick();
      const phone = seconds === 1 ? { count: 0, confidence: null } : { count: 1, confidence: .45 };
      s.worker.onmessage({ data: { type: 'result', face: observe(1), phone, errors: {} } });
    }
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(s.events.length, 1);
    assert.equal(s.monitor.phoneEventsSaved, delivered ? 1 : 0);
    assert.equal(s.monitor.phoneEventsUnconfirmed, delivered ? 0 : 1);
    assert.match(s.statuses.at(-1), delivered ? /Phone events saved: 1; delivery unconfirmed: 0/
      : /Phone events saved: 0; delivery unconfirmed: 1/);
    await s.monitor.stop();
  }
});
