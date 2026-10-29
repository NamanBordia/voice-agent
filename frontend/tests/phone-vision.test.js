import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { PhoneRules } from '../phone-rules.js';
import { VisionSummary } from '../vision-summary.js';

const config = { fps: 4, phone_enabled: true, phone_fps: 2, phone_confidence: .7, phone_sustain_secs: 2, phone_cooldown_secs: 15 };
const seen = (confidence = .9) => ({ count: 1, confidence });

test('phone events require sustained observations and cooldown; gaps and low scores reset them', () => {
  const rules = new PhoneRules(config);
  for (const time of [0, .5, 1, 1.5]) assert.deepEqual(rules.update(seen(), time), []);
  const event = rules.update(seen(), 2)[0];
  assert.equal(event.type, 'phone_detected'); assert.equal(event.duration_secs, 2); assert.equal(event.face_count, null);
  for (let time = 2.5; time < 17; time += .5) assert.deepEqual(rules.update(seen(), time), []);
  assert.equal(rules.update(seen(), 17).length, 1);
  const other = new PhoneRules(config);
  other.update(seen(), 0); other.update(seen(), .5);
  assert.deepEqual(other.update(seen(.2), 1), []);
  other.update(seen(), 1.5); assert.deepEqual(other.update(seen(), 2), []);
  assert.deepEqual(other.update(seen(), 10), []);
  assert.deepEqual(other.update({ count: 0, confidence: null }, 10.5), []);
});

test('per-detector cumulative snapshots preserve unknown time and do not double-count retries', () => {
  const summary = new VisionSummary({ callSid: 'call1', elapsed: 10, consent: true, config });
  summary.success('face', 10.25); summary.success('face', 10.5);
  summary.success('phone', 10.5); summary.success('phone', 11);
  const first = summary.snapshot(11);
  assert.equal(first.face.covered_secs, .25); assert.equal(first.face.unknown_secs, .75);
  assert.equal(first.phone.frames_analyzed, 2); assert.equal(first.phone.covered_secs, .5);
  assert.deepEqual(summary.snapshot(11), first);
  summary.success('face', 20); // A long gap is not successful analysis.
  const closed = summary.snapshot(21, true);
  assert.equal(closed.face.covered_secs, .25); assert.equal(closed.face.unknown_secs, 10.75);
  assert.equal(closed.phone.frames_analyzed, 2); assert.equal(closed.end_secs, 21);
  assert.deepEqual(summary.snapshot(100), closed);
  summary.success('phone', 101); assert.deepEqual(summary.snapshot(101), closed);
  const resumed = new VisionSummary({ callSid: 'call2', elapsed: 21, consent: true, config });
  assert.equal(resumed.snapshot(22).phone.frames_analyzed, 0);
});

test('disabled detectors make no analysis claim and failure gaps stay bounded', () => {
  const disabled = new VisionSummary({ callSid: 'c', elapsed: 0, consent: false, config });
  disabled.success('face', 1); disabled.failure('phone', 'worker_error', 2);
  assert.equal(disabled.snapshot(100).face.unknown_secs, 0);
  assert.equal(disabled.snapshot(100).phone.frames_analyzed, 0);
  const summary = new VisionSummary({ callSid: 'c', elapsed: 0, consent: true, config });
  for (let time = 1; time < 300; time += 3) { summary.success('phone', time); summary.success('phone', time + .5); }
  const snapshot = summary.snapshot(300);
  assert.equal(snapshot.phone.gaps.length, 64); assert.equal(snapshot.phone.history_truncated, true);
  assert.equal(snapshot.phone.covered_secs, 50);
});

let moduleId = 0;
async function worker({ phoneLoadFails = false, phoneInferenceFails = false, phoneScore = .85 } = {}) {
  const outputs = []; const options = []; let faceCalls = 0; let phoneCalls = 0; let faceClosed = 0; let phoneClosed = 0;
  const face = { detectForVideo() { faceCalls += 1; return { detections: [] }; }, close() { faceClosed += 1; } };
  const phone = { detectForVideo() { phoneCalls += 1; if (phoneInferenceFails) throw new Error('private device error');
    return { detections: [{ categories: [{ categoryName: 'remote', score: .99 }] },
      { categories: [{ categoryName: 'cell phone', score: phoneScore }] }] }; }, close() { phoneClosed += 1; } };
  globalThis.testVision = { FilesetResolver: { async forVisionTasks(path) { assert.equal(path, '/static/vision/wasm'); return {}; } },
    FaceDetector: { async createFromOptions(_, config) { options.push(config); return face; } },
    ObjectDetector: { async createFromOptions(_, config) { options.push(config); if (phoneLoadFails) throw new Error('model missing'); return phone; } } };
  globalThis.OffscreenCanvas = class {};
  globalThis.self = { postMessage(data) { outputs.push(data); }, close() {} };
  const source = (await readFile(new URL('../camera-worker.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/, 'const { FaceDetector, ObjectDetector, FilesetResolver } = globalThis.testVision;\n');
  await import(`data:text/javascript;base64,${Buffer.from(`${source}\n// worker ${moduleId++}`).toString('base64')}`);
  return { outputs, options, message: (data) => self.onmessage({ data }),
    counts: () => ({ faceCalls, phoneCalls, faceClosed, phoneClosed }) };
}

test('actual worker uses local CPU models, phone allowlist and slower phone cadence', async () => {
  const w = await worker(); let closed = 0;
  await w.message({ type: 'init', confidence: .6, ...config });
  assert.deepEqual(w.options[1].categoryAllowlist, ['cell phone']);
  assert.equal(w.options[1].scoreThreshold, .3);
  assert.equal(w.options[1].baseOptions.delegate, 'CPU');
  assert.match(w.options[1].baseOptions.modelAssetPath, /^\/static\/vision\//);
  for (const timestamp of [0, 250, 500]) await w.message({ type: 'frame', timestamp, bitmap: { width: 640, height: 360, close() { closed += 1; } } });
  assert.equal(w.counts().faceCalls, 3); assert.equal(w.counts().phoneCalls, 2); assert.equal(closed, 3);
  assert.equal(w.outputs.at(-1).phone.count, 1); assert.equal(w.outputs.at(-1).phone.confidence, .85);
  await w.message({ type: 'close' });
  assert.equal(w.counts().faceClosed, 1); assert.equal(w.counts().phoneClosed, 1);
});

test('weak phone candidates remain local and cannot bypass the configured event threshold', async () => {
  const w = await worker({ phoneScore: .45 });
  await w.message({ type: 'init', confidence: .6, ...config });
  const rules = new PhoneRules(config);
  for (const timestamp of [0, 500, 1000, 1500, 2000, 2500]) {
    await w.message({ type: 'frame', timestamp, bitmap: { close() {} } });
    const observation = w.outputs.at(-1).phone;
    assert.equal(observation.confidence, .45);
    assert.deepEqual(rules.update(observation, timestamp / 1000), []);
  }
  assert.match(rules.describe(), /score below event threshold/);
  assert.match(rules.describe(), /Events created: 0/);
  await w.message({ type: 'close' });
});

test('local rule diagnostics distinguish an isolated miss, reset after repeated misses, and cooldown', () => {
  const rules = new PhoneRules(config);
  rules.update(seen(), 0); rules.update(seen(), .5);
  assert.match(rules.describe(), /sighting span 0.5\/2s/);
  rules.update({ count: 0, confidence: null }, 1);
  assert.match(rules.describe(), /One missed phone sample/);
  rules.update({ count: 0, confidence: null }, 1.5);
  assert.match(rules.describe(), /timer reset: no phone candidate/);
  rules.update(seen(), 2);
  assert.match(rules.describe(), /sighting span 0.0\/2s/);
  for (const seconds of [2.5, 3, 3.5, 4]) rules.update(seen(), seconds);
  assert.match(rules.describe(), /event queued/);
  for (const seconds of [4.5, 5, 5.5, 6]) rules.update(seen(), seconds);
  assert.match(rules.describe(), /cooldown/);
  assert.equal(rules.eventsCreated, 1);
});

test('a sustained phone with an isolated missed sample creates an event only after enough positive samples', () => {
  const rules = new PhoneRules({ ...config, phone_confidence: .3 });
  for (const [time, observation] of [[0, seen(.45)], [.5, seen(.4)], [1, { count: 0, confidence: null }],
    [1.5, seen(.42)], [2, seen(.38)]]) assert.deepEqual(rules.update(observation, time), []);
  const [event] = rules.update(seen(.41), 2.5);
  assert.equal(event.type, 'phone_detected');
  assert.equal(event.start_secs, 0); assert.equal(event.duration_secs, 2.5);
  assert.equal(rules.eventsCreated, 1);
});

test('a single flash, repeated misses, low scores and a long gap never create a sustained event', () => {
  for (const mode of ['flash', 'repeated misses', 'low scores', 'long gap']) {
    const rules = new PhoneRules(config);
    rules.update(seen(), 0);
    if (mode === 'long gap') assert.deepEqual(rules.update(seen(), 4), []);
    else for (const time of [.5, 1, 1.5, 2, 2.5, 3]) {
      const observation = mode === 'low scores' ? seen(.4)
        : mode === 'repeated misses' && time === 2 ? seen() : { count: 0, confidence: null };
      assert.deepEqual(rules.update(observation, time), []);
    }
    assert.equal(rules.eventsCreated, 0);
  }
});

test('phone load or inference failure retains healthy face results and exposes no raw error text', async () => {
  for (const cause of ['load', 'inference']) {
    const w = await worker({ phoneLoadFails: cause === 'load', phoneInferenceFails: cause === 'inference' });
    await w.message({ type: 'init', confidence: .6, ...config });
    await w.message({ type: 'frame', timestamp: 1, bitmap: { close() {} } });
    assert.equal(w.outputs.at(-1).face.count, 0);
    assert.equal(w.counts().faceClosed, 0);
    assert.equal(w.outputs.some((output) => output.errors?.phone === (cause === 'load' ? 'model_load_failed' : 'inference_failed')), true);
    assert.doesNotMatch(JSON.stringify(w.outputs), /private|model missing/);
    await w.message({ type: 'close' });
  }
});

test('disabled phone detection never loads or runs the object model', async () => {
  const w = await worker(); await w.message({ type: 'init', confidence: .6, phone_enabled: false });
  assert.equal(w.options.length, 1);
  await w.message({ type: 'frame', timestamp: 1, bitmap: { close() {} } });
  assert.equal(w.counts().phoneCalls, 0); assert.equal(w.outputs.at(-1).phone, null);
  await w.message({ type: 'close' });
});
