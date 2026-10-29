import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';

const element = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const startButton = /** @type {HTMLButtonElement} */ (element('start'));
const muteButton = /** @type {HTMLButtonElement} */ (element('mute'));
const endButton = /** @type {HTMLButtonElement} */ (element('end'));
const retryButton = /** @type {HTMLButtonElement} */ (element('retry-save'));
const audio = /** @type {HTMLAudioElement} */ (element('bot-audio'));
const meter = /** @type {HTMLMeterElement} */ (element('mic-level'));
const transcript = element('transcript');
const participation = globalThis.interviewParticipation || null;
const endEndpoint = (sid) => participation?.endEndpoint || `/api/sessions/${encodeURIComponent(sid)}/end`;
let thinking = false;
let client = null;
let generation = 0;
let callSid = null;
let muted = false;
let botReady = false;
let uiReady = false;
let greeting = true;
let botSpeaking = false;
let stopping = false;
let assistantRow = null;
let userDraft = null;
let pendingSaveSid = null;

function syncMic() {
  if (!client) return;
  const enabled = !muted && !greeting && !botSpeaking && !stopping && !(participation && thinking);
  // Track gating is immediate even when a media manager changes devices asynchronously.
  const track = client.tracks?.()?.local?.audio;
  if (track) track.enabled = enabled;
  try {
    Promise.resolve(client.enableMic(enabled)).catch(() => error('Could not change microphone state.'));
  } catch { error('Could not change microphone state.'); }
}

async function configureInput(active) {
  const track = active.tracks?.()?.local?.audio;
  if (!track?.applyConstraints) return;
  const supported = navigator.mediaDevices?.getSupportedConstraints?.() || {};
  const constraints = Object.fromEntries(['echoCancellation', 'noiseSuppression', 'autoGainControl']
    .filter((name) => supported[name]).map((name) => [name, true]));
  try { await track.applyConstraints(constraints); }
  catch { console.warn('Microphone uses its available browser audio settings.'); }
}

function reportInput() {
  const track = client?.tracks?.()?.local?.audio;
  if (!track?.getSettings) return;
  const settings = track.getSettings();
  const data = Object.fromEntries(['sampleRate', 'channelCount', 'echoCancellation', 'noiseSuppression', 'autoGainControl']
    .filter((name) => settings[name] !== undefined).map((name) => [name, settings[name]]));
  client.sendClientMessage('audio-diagnostics', { ...data, label: (track.label || '').slice(0, 120) });
  element('input-device').textContent = `Microphone: ${track.label || 'browser default'}`;
}

function status(text, state = 'idle') {
  element('status').textContent = text;
  element('conversation').setAttribute('data-state', state);
}
function error(text) {
  element('error').textContent = text;
  element('error').hidden = !text;
}

function row(role, text, draft = false) {
  element('empty')?.remove();
  const container = document.createElement('div');
  container.className = `utterance utterance--${role === 'You' ? 'user' : 'agent'}${draft ? ' draft' : ''}`;
  const label = document.createElement('strong');
  label.textContent = role;
  const body = document.createElement('span');
  body.setAttribute('dir', 'auto');
  body.textContent = text;
  container.append(label, body);
  transcript.append(container);
  transcript.scrollTop = transcript.scrollHeight;
  return body;
}

async function playbackReady() {
  if (!client || !botReady || !audio.srcObject || uiReady || stopping) return;
  const active = client;
  const current = generation;
  try {
    await audio.play();
    if (client !== active || generation !== current || uiReady || stopping) return;
    uiReady = true;
    element('audio-enable').hidden = true;
    client.sendClientMessage('ui-ready', {});
    reportInput();
    status('Preparing your greeting…', 'preparing');
  } catch {
    if (client !== active || generation !== current || stopping) return;
    element('audio-enable').hidden = false;
    status('Click Enable audio to hear the agent.', 'attention');
  }
}

function resetControls() {
  startButton.disabled = Boolean(pendingSaveSid);
  muteButton.disabled = true;
  endButton.disabled = true;
  meter.value = 0;
  element('audio-enable').hidden = true;
  audio.pause();
  audio.srcObject = null;
}

async function saveConversation(sid) {
  pendingSaveSid = sid;
  const response = await fetch(endEndpoint(sid), {
    method: 'POST', headers: participation?.headers, signal: AbortSignal.timeout(65000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || 'Could not confirm cleanup.');
  if (result.saved && result.history_url) {
    const link = /** @type {HTMLAnchorElement} */ (element('history-link'));
    link.href = result.history_url;
    element('history').hidden = false;
    pendingSaveSid = null;
  }
  if (result.saved && participation) pendingSaveSid = null;
  if (result.error) error(result.error);
  return result.saved;
}

async function stop() {
  if (stopping || !client) return;
  stopping = true;
  const active = client;
  syncMic();
  endButton.disabled = true;
  muteButton.disabled = true;
  status('Ending and saving…', 'saving');
  try {
    if (participation?.beforeEnd) await participation.beforeEnd();
    // A candidate offer can reserve the runner before the session message arrives.
    const cleanupSid = callSid || participation?.sessionId;
    if (cleanupSid) {
      await saveConversation(cleanupSid);
    }
  } catch (cause) {
    error(cause.message || 'Could not confirm whether the conversation was saved.');
  } finally {
    generation += 1; // Ignore late callbacks from the previous connection.
    client = null;
    await active.disconnect().catch(() => {});
    resetControls();
    stopping = false;
    retryButton.hidden = !pendingSaveSid;
    status(pendingSaveSid ? 'Conversation saving is not confirmed. Click Retry saving.' : 'Conversation ended.',
      pendingSaveSid ? 'attention' : 'idle');
    await participation?.onEnd?.();
  }
}

startButton.addEventListener('click', async () => {
  const current = ++generation;
  const valid = () => current === generation && !stopping;
  startButton.disabled = true;
  error('');
  element('history').hidden = true;
  transcript.replaceChildren();
  callSid = null;
  muted = false;
  botReady = uiReady = botSpeaking = stopping = thinking = false;
  greeting = true;
  assistantRow = userDraft = null;
  muteButton.textContent = 'Mute';
  muteButton.setAttribute('aria-pressed', 'false');
  status('Allow microphone access to connect…', 'connecting');
  let active;
  try {
    if (participation?.beforeStart && !await participation.beforeStart()) {
      resetControls(); return;
    }
    active = new PipecatClient({
      transport: participation?.createTransport?.() || new SmallWebRTCTransport({ iceServers: [] }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTrackStarted: (track, participant) => {
          if (!valid() || participant?.local || track.kind !== 'audio') return;
          audio.srcObject = new MediaStream([track]);
          void playbackReady();
        },
        onBotReady: () => {
          if (!valid()) return;
          botReady = true;
          void playbackReady();
        },
        onServerMessage: (data) => {
          if (!valid()) return;
          if (data.type === 'session') {
            callSid = data.call_sid;
            participation?.onSession?.(callSid);
            endButton.disabled = false;
            muteButton.disabled = false;
          }
        },
        onLocalAudioLevel: (level) => {
          if (!valid()) return;
          meter.value = Number.isFinite(level) ? Math.min(1, Math.max(0, level)) : 0;
        },
        onBotStartedSpeaking: () => {
          if (!valid()) return;
          botSpeaking = true;
          syncMic();
          status('Agent speaking…', 'speaking');
        },
        onBotStoppedSpeaking: () => {
          if (!valid() || !botSpeaking) return;
          botSpeaking = false;
          thinking = false;
          assistantRow = null;
          if (greeting) {
            greeting = false;
          }
          syncMic();
          status(muted ? 'Microphone muted.' : 'Listening…', muted ? 'idle' : 'listening');
        },
        onBotTtsText: (data) => {
          if (!valid() || !data.text) return;
          if (!assistantRow) assistantRow = row('Agent', '');
          assistantRow.textContent += `${assistantRow.textContent ? ' ' : ''}${data.text}`;
          transcript.scrollTop = transcript.scrollHeight;
        },
        onUserTranscript: (data) => {
          if (!valid() || greeting || muted || botSpeaking || !data.text) return;
          if (!userDraft) userDraft = row('You', '', true);
          userDraft.textContent = data.text;
          if (data.final) {
            userDraft.parentElement.classList.remove('draft');
            userDraft = null;
            assistantRow = null;
            status('Thinking…', 'preparing');
            if (participation) { thinking = true; syncMic(); }
          }
        },
        onBotLlmStarted: () => { if (valid()) status('Thinking…', 'preparing'); },
        onError: (event) => {
          if (!valid()) return;
          error(event.data?.error || event.error || event.message || 'The agent reported a provider error.');
          void stop();
        },
        onDisconnected: () => { if (valid() && !stopping) void stop(); },
      },
    });
  } catch (cause) {
    error(cause.message || 'This browser could not initialize audio.');
    resetControls();
    status('Could not initialize audio.', 'error');
    await participation?.onEnd?.();
    return;
  }
  client = active;
  try {
    await active.initDevices();
    if (!valid()) return;
    syncMic();
    await configureInput(active);
    if (!valid()) return;
    endButton.disabled = false; // Allows canceling a connection before a session ID arrives.
    status('Connecting…', 'connecting');
    await active.connect({ webrtcRequestParams: { endpoint: participation?.offerEndpoint || '/api/offer', headers: participation?.headers } });
  } catch (cause) {
    if (!valid()) return;
    error(cause.message || 'Connection failed. Check microphone permissions and Settings.');
    await stop();
  }
});

muteButton.addEventListener('click', () => {
  muted = !muted;
  syncMic();
  muteButton.textContent = muted ? 'Unmute' : 'Mute';
  muteButton.setAttribute('aria-pressed', String(muted));
  if (!botSpeaking && !greeting) status(muted ? 'Microphone muted.' : 'Listening…', muted ? 'idle' : 'listening');
});
endButton.addEventListener('click', () => void stop());
retryButton.addEventListener('click', async () => {
  if (!pendingSaveSid || retryButton.disabled) return;
  retryButton.disabled = true;
  error('');
  status('Checking and saving…', 'saving');
  try {
    await saveConversation(pendingSaveSid);
  } catch (cause) {
    error(cause.message || 'Saving is not confirmed. Retry shortly.');
  } finally {
    retryButton.disabled = false;
    retryButton.hidden = !pendingSaveSid;
    startButton.disabled = Boolean(pendingSaveSid);
    status(pendingSaveSid ? 'Saving is not confirmed. Retry shortly.' : 'Conversation saved.',
      pendingSaveSid ? 'attention' : 'saved');
    if (!pendingSaveSid) await participation?.onEnd?.();
  }
});
element('audio-enable').addEventListener('click', () => void playbackReady());
window.addEventListener('pagehide', () => {
  client?.enableMic(false);
  participation?.onPageHide?.();
  const cleanupSid = callSid || (client && participation?.sessionId);
  if (cleanupSid) void fetch(endEndpoint(cleanupSid), { method: 'POST', headers: participation?.headers, keepalive: true }).catch(() => {});
  void client?.disconnect().catch(() => {});
});
