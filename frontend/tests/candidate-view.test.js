import test from 'node:test';
import assert from 'node:assert/strict';
import { refreshCandidateView } from '../candidate-view.js';

function page() {
  const elements = Object.fromEntries(['candidate-root', 'conversation', 'end', 'transcript', 'current-question', 'gate-explanation', 'camera-preview', 'mute', 'device-settings', 'connection-state']
    .map((id) => [id, { dataset: {}, textContent: '', disabled: true, getAttribute: () => 'false' }]));
  const prompts = [];
  elements.transcript.querySelectorAll = () => prompts;
  return { elements, prompts, doc: { getElementById: (id) => elements[id] } };
}

test('candidate presentation reflects actual state and spoken text without replacing media nodes', () => {
  const p = page(); const video = p.elements['camera-preview'];
  video.srcObject = { getVideoTracks: () => [{ readyState: 'live' }] };
  p.elements.conversation.dataset.state = 'speaking';
  p.prompts.push({ textContent: 'How would you compare the characters?' });
  refreshCandidateView(p.doc);
  assert.equal(p.elements['current-question'].textContent, p.prompts[0].textContent);
  assert.equal(p.elements['candidate-root'].dataset.mode, 'live');
  assert.match(p.elements['gate-explanation'].textContent, /temporarily gated/);
  assert.equal(video.dataset.stream, 'live');
  assert.equal(p.elements['device-settings'].open, false);
  p.elements['device-settings'].open = true;
  p.elements.conversation.dataset.state = 'listening'; refreshCandidateView(p.doc);
  assert.match(p.elements['gate-explanation'].textContent, /Your turn/);
  assert.equal(p.elements['device-settings'].open, true); // User can reopen settings without the observer fighting it.
  p.elements.conversation.dataset.state = 'idle'; video.srcObject = null; refreshCandidateView(p.doc);
  assert.equal(p.elements['candidate-root'].dataset.mode, 'live');
  assert.equal(video.dataset.stream, 'none');
  assert.equal(p.elements['camera-preview'], video);
  assert.match(p.elements['gate-explanation'].textContent, /No active/);
  p.elements['candidate-root'].dataset.sessionStatus = 'paused'; refreshCandidateView(p.doc);
  assert.match(p.elements['connection-state'].textContent, /Paused/);
});

test('presentation does not invent questions, readiness or connections before participation', () => {
  const p = page(); p.elements.conversation.dataset.state = 'idle';
  p.elements['current-question'].textContent = 'Question pending';
  refreshCandidateView(p.doc);
  assert.equal(p.elements['candidate-root'].dataset.mode, undefined);
  assert.equal(p.elements['current-question'].textContent, 'Question pending');
  assert.equal(p.elements['camera-preview'].dataset.stream, 'none');
  assert.match(p.elements['gate-explanation'].textContent, /No active/);
});
