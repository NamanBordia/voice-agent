// Device/provider-free tests of the actual page module's lifecycle callbacks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let moduleId = 0;

class Element {
  constructor() {
    this.handlers = {};
    this.children = [];
    this.hidden = false;
    this.textContent = '';
    this.className = '';
    this.classList = {
      remove: (...tokens) => { this.className = this.className.split(' ').filter((token) => !tokens.includes(token)).join(' '); },
    };
  }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  append(...elements) { for (const e of elements) { e.parentElement = this; this.children.push(e); } }
  replaceChildren() { this.children = []; }
  remove() {}
  setAttribute(name, value) { this[name] = value; }
  pause() {}
  play() { return Promise.resolve(); }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test('microphone meter accepts only finite levels in its zero-to-one range', async () => {
  const { elements, client } = await page();
  let shown = 0;
  Object.defineProperty(elements['mic-level'], 'value', {
    get() { return shown; },
    set(level) {
      if (!Number.isFinite(level)) throw new TypeError('Non-finite meter value');
      shown = level;
    },
  });
  await elements.start.handlers.click();
  for (const [level, expected] of [
    [NaN, 0], [Infinity, 0], [-Infinity, 0], [undefined, 0], [null, 0],
    ['0.5', 0], [-0.3, 0], [1.4, 1], [0.42, 0.42],
  ]) {
    assert.doesNotThrow(() => client().callbacks.onLocalAudioLevel(level));
    assert.equal(shown, expected);
  }
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ saved: true, history_url: '/calls/saved' }) });
  elements.end.handlers.click();
  await settle();
  client().callbacks.onLocalAudioLevel(0.8);
  assert.equal(shown, 0); // Old connection callbacks cannot revive the reset meter.
});

async function page({ denied = false } = {}) {
  const ids = ['conversation', 'start', 'mute', 'end', 'bot-audio', 'mic-level', 'transcript', 'status', 'error', 'empty', 'audio-enable', 'history', 'history-link', 'retry-save', 'input-device'];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
  globalThis.document = { getElementById: (id) => elements[id], createElement: () => new Element() };
  globalThis.window = { addEventListener() {} };
  globalThis.MediaStream = class {};
  let current;
  globalThis.PipecatClient = class {
    constructor(options) { current = this; this.callbacks = options.callbacks; this.micChanges = []; this.sent = []; }
    async initDevices() { if (denied) throw new Error('Microphone permission denied'); }
    enableMic(enabled) { this.micChanges.push(enabled); }
    async connect() {
      this.callbacks.onTrackStarted({ kind: 'audio' }, { local: false });
      this.callbacks.onBotReady();
      this.callbacks.onServerMessage({ type: 'session', call_sid: `browser_${'a'.repeat(32)}` });
    }
    sendClientMessage(type) { this.sent.push(type); }
    async disconnect() { this.disconnected = true; this.callbacks.onDisconnected(); }
  };
  globalThis.SmallWebRTCTransport = class {};
  const source = (await readFile(new URL('../demo.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '');
  await import(`data:text/javascript;base64,${Buffer.from(`${source}\n// case ${moduleId++}`).toString('base64')}`);
  return { elements, client: () => current };
}

test('input stays muted until greeting completion and readiness is sent once', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
  assert.deepEqual(client().micChanges, [false]);
  client().callbacks.onBotReady();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
  client().callbacks.onBotStartedSpeaking();
  client().callbacks.onBotStoppedSpeaking();
  assert.deepEqual(client().micChanges, [false, false, true]);
  assert.equal(elements.status.textContent, 'Listening…');
  assert.equal(elements.conversation['data-state'], 'listening');
});

test('mute chosen during greeting is respected after speech', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  elements.mute.handlers.click();
  client().callbacks.onBotStartedSpeaking();
  client().callbacks.onBotStoppedSpeaking();
  assert.equal(client().micChanges.at(-1), false);
  assert.equal(elements.mute.textContent, 'Unmute');
});

test('blocked playback provides an explicit recovery action before greeting', async () => {
  const { elements, client } = await page();
  elements['bot-audio'].play = () => Promise.reject(new Error('autoplay blocked'));
  await elements.start.handlers.click();
  await settle();
  assert.deepEqual(client().sent, []);
  assert.equal(elements['audio-enable'].hidden, false);
  assert.equal(elements.conversation['data-state'], 'attention');
  elements['bot-audio'].play = () => Promise.resolve();
  elements['audio-enable'].handlers.click();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
});

test('End waits for the saved response, disconnects, and ignores late callbacks', async () => {
  const { elements, client } = await page();
  let complete;
  globalThis.fetch = () => new Promise((resolve) => { complete = resolve; });
  await elements.start.handlers.click();
  await settle();
  elements.end.handlers.click();
  assert.equal(elements.start.disabled, true);
  complete({ ok: true, json: async () => ({ saved: true, history_url: '/calls/saved' }) });
  await settle();
  assert.equal(client().disconnected, true);
  assert.equal(elements.start.disabled, false);
  assert.equal(elements.history.hidden, false);
  client().callbacks.onBotStartedSpeaking();
  assert.equal(elements.status.textContent, 'Conversation ended.');
});

test('denied microphone resets controls and displays a useful error', async () => {
  const { elements, client } = await page({ denied: true });
  await elements.start.handlers.click();
  assert.equal(elements.start.disabled, false);
  assert.equal(client().disconnected, true);
  assert.match(elements.error.textContent, /Microphone permission denied/);
});

test('overlapping playback callbacks send readiness only once', async () => {
  const { elements, client } = await page();
  const pending = [];
  elements['bot-audio'].play = () => new Promise((resolve) => pending.push(resolve));
  await elements.start.handlers.click();
  client().callbacks.onBotReady();
  for (const resolve of pending) resolve();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
});

test('every bot reply mutes input and manual mute survives the speaking callbacks', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  client().callbacks.onBotStartedSpeaking();
  client().callbacks.onBotStoppedSpeaking();
  assert.equal(client().micChanges.at(-1), true);
  client().callbacks.onBotStartedSpeaking();
  assert.equal(client().micChanges.at(-1), false);
  assert.equal(elements.conversation['data-state'], 'speaking');
  elements.mute.handlers.click();
  client().callbacks.onBotStoppedSpeaking();
  assert.equal(client().micChanges.at(-1), false);
  assert.equal(elements.status.textContent, 'Microphone muted.');
  assert.equal(elements.conversation['data-state'], 'idle');
});

test('failed save stays visible and retry uses the original call without another connection', async () => {
  const { elements, client } = await page();
  const urls = [];
  let attempt = 0;
  globalThis.fetch = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => ++attempt === 1
      ? { saved: false, error: 'Conversation is still ending. Retry saving shortly.' }
      : { saved: true, history_url: '/calls/saved' } };
  };
  await elements.start.handlers.click();
  elements.end.handlers.click();
  await settle();
  assert.equal(elements['retry-save'].hidden, false);
  assert.equal(elements.start.disabled, true);
  assert.match(elements.status.textContent, /not confirmed/);
  assert.equal(elements.conversation['data-state'], 'attention');
  assert.equal(client().disconnected, true);
  await elements['retry-save'].handlers.click();
  assert.deepEqual(urls, [urls[0], urls[0]]);
  assert.equal(elements.history.hidden, false);
  assert.equal(elements.start.disabled, false);
  assert.equal(elements['retry-save'].hidden, true);
  assert.equal(elements.status.textContent, 'Conversation saved.');
  assert.equal(elements.conversation['data-state'], 'saved');
});

test('message styling keeps streaming and finalized transcripts in their original nodes', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  await settle();
  const callbacks = client().callbacks;
  callbacks.onBotStartedSpeaking();
  callbacks.onBotTtsText({ text: 'नमस्ते!' });
  const agent = elements.transcript.children[0];
  const agentBody = agent.children[1];
  callbacks.onBotTtsText({ text: 'How can I help?' });
  assert.equal(elements.transcript.children.length, 1);
  assert.equal(agent.children[0].textContent, 'Agent');
  assert.match(agent.className, /utterance--agent/);
  assert.equal(agent.children[1], agentBody);
  assert.equal(agentBody.textContent, 'नमस्ते! How can I help?');
  assert.equal(agentBody.dir, 'auto');
  callbacks.onBotStoppedSpeaking();

  callbacks.onUserTranscript({ text: 'मुझे', final: false });
  const user = elements.transcript.children[1];
  const userBody = user.children[1];
  assert.match(user.className, /utterance--user.*draft/);
  callbacks.onUserTranscript({ text: 'मुझे help चाहिए।', final: false });
  callbacks.onUserTranscript({ text: 'मुझे help चाहिए। <script>plain text</script>', final: true });
  assert.equal(elements.transcript.children.length, 2);
  assert.equal(user.children[0].textContent, 'You');
  assert.equal(user.children[1], userBody);
  assert.equal(userBody.textContent, 'मुझे help चाहिए। <script>plain text</script>');
  assert.doesNotMatch(user.className, /\bdraft\b/);
  assert.equal(elements.conversation['data-state'], 'preparing');

  callbacks.onBotTtsText({ text: 'ज़रूर।' });
  assert.equal(elements.transcript.children.length, 3);
  assert.equal(elements.transcript.children[0], agent);
  assert.equal(elements.transcript.children[1], user);
});

test('a network failure preserves the save identifier and never reports a successful save', async () => {
  const { elements } = await page();
  globalThis.fetch = async () => { throw new Error('network failed'); };
  await elements.start.handlers.click();
  elements.end.handlers.click();
  await settle();
  assert.match(elements.error.textContent, /network failed/);
  assert.equal(elements['retry-save'].hidden, false);
  assert.match(elements.status.textContent, /not confirmed/);
});
