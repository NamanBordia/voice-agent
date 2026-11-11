import test from 'node:test';
import assert from 'node:assert/strict';
import { connectionFailureText } from '../connection-errors.js';

test('device-helper errorMsg objects and string rejections retain the real failure', async () => {
  assert.equal(await connectionFailureText({ errorMsg: 'Device helper script could not load' }, 'audio helper'),
    'Audio device helper could not load: Device helper script could not load');
  assert.equal(await connectionFailureText('Signaling rejected'), 'Voice connection failed: Signaling rejected');
  assert.match(await connectionFailureText(undefined), /Voice connection failed/);
});

test('device errors distinguish permission, absent device and an unreadable microphone', async () => {
  assert.match(await connectionFailureText({ name: 'NotAllowedError' }, 'microphone'), /Chrome site settings, allow Microphone/);
  assert.match(await connectionFailureText({ name: 'NotFoundError' }, 'microphone'), /No microphone/);
  assert.match(await connectionFailureText({ name: 'NotReadableError' }, 'microphone'), /Close other apps/);
});

test('rejected HTTP responses retain backend details and never show validation input payloads', async () => {
  assert.equal(await connectionFailureText(Response.json({ detail: 'A conversation is already active' }, { status: 409 })),
    'Voice connection failed: HTTP 409: A conversation is already active');
  const text = await connectionFailureText(Response.json({ detail: [{ loc: ['body', 'sdp'], msg: 'Required', input: 'private-offer' }] }, { status: 422 }));
  assert.match(text, /body.sdp: Required/); assert.ok(!text.includes('private-offer'));
});
