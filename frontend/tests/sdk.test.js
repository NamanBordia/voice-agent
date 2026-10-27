// Exercise the installed SDK's real message parsing; transport/device I/O is stubbed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { PipecatClient, RTVI_PROTOCOL_VERSION } from '@pipecat-ai/client-js';

test('RTVI 1.3 server messages reach the callbacks used by the page', async () => {
  globalThis.window = { navigator: { userAgent: 'Node protocol test' } };
  globalThis.window = { navigator: { userAgent: 'Node protocol test' } };
  assert.equal(RTVI_PROTOCOL_VERSION, '1.3.0');
  const events = [];
  const transport = {
    state: 'ready',
    maxMessageSize: 65536,
    initialize(options, handler) { this.receive = handler; },
    async disconnect() {},
    sendMessage(message) { this.sent = message; },
  };
  const client = new PipecatClient({ transport, enableMic: false, enableCam: false, callbacks: {
    onBotReady: (data) => events.push(['ready', data.version]),
    onServerMessage: (data) => events.push(['session', data.call_sid]),
    onUserTranscript: (data) => events.push(['user', data.text, data.final]),
    onBotTtsText: (data) => events.push(['agent', data.text]),
    onBotStartedSpeaking: () => events.push(['speaking']),
    onBotStoppedSpeaking: () => events.push(['stopped']),
    onError: (event) => events.push(['error', event.data.error]),
  } });
  const send = (type, data = {}) => transport.receive({ label: 'rtvi-ai', id: 'test', type, data });
  send('bot-ready', { version: '1.3.0' });
  send('server-message', { type: 'session', call_sid: 'browser_test' });
  send('user-transcription', { text: 'hello', final: true, user_id: 'user', timestamp: 'now' });
  send('bot-tts-text', { text: 'Hi.' });
  send('bot-started-speaking');
  send('bot-stopped-speaking');
  send('error', { error: 'Speech provider failed', fatal: false });
  client.sendClientMessage('ui-ready', {});
  assert.equal(transport.sent.type, 'client-message');
  assert.deepEqual(transport.sent.data, { t: 'ui-ready', d: {} });
  assert.deepEqual(events, [['ready', '1.3.0'], ['session', 'browser_test'],
    ['user', 'hello', true], ['agent', 'Hi.'], ['speaking'], ['stopped'], ['error', 'Speech provider failed']]);
  await client.disconnect();
});
