import { CameraMonitor } from './camera-monitor.js';
import Daily from '@daily-co/daily-js';
import { createCandidateHeaders } from './candidate-http.js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { candidateTransport } from './candidate-transport.js';
const CandidateTransport = candidateTransport(SmallWebRTCTransport);
const el = (id) => document.getElementById(id);
const id = el('candidate-root').dataset.sessionId;
const token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem(`interview-token-${id}`) || '';
if (token) sessionStorage.setItem(`interview-token-${id}`, token);
history.replaceState(null, '', location.pathname); // Keep bearer tokens out of referrers and visible URL after entry.
const { signalingHeaders, jsonHeaders: headers } = createCandidateHeaders(token);
const base = `/api/candidate/${id}`;
let info; let camera = null; let monitor = null; let checkStream = null; let audioContext = null; let meterTimer = null;
let prepared = false; let micChecked = false; let running = false; let sequence = 0; let heartbeatSequence = 0; let sendQueue = Promise.resolve();
let queuedUpdates = 0;
let cameraCallSid = null; let deliveryGeneration = 0; let exiting = false;
let cameraGeneration = 0;
const message = (text) => { el('preparation-status').textContent = text; };
async function request(path = '', method = 'GET', body, keepalive = false) {
  const response = await fetch(`${base}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), keepalive, signal: keepalive ? undefined : AbortSignal.timeout(10000) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Interview request failed.');
  return result;
}
function stopCheck() {
  clearInterval(meterTimer); meterTimer = null;
  checkStream?.getTracks().forEach((track) => track.stop()); checkStream = null;
  void audioContext?.close().catch(() => {}); audioContext = null; el('check-level').value = 0;
}
function stopCamera(notify = true, reason = 'connection_ended') {
  cameraGeneration += 1; // Invalidate a permission request still in flight.
  const delivered = monitor?.stop(notify, reason); monitor = null;
  camera?.getTracks().forEach((track) => track.stop()); camera = null;
  el('camera-preview').srcObject = null;
  return delivered || Promise.resolve();
}
async function openCamera(requireConsent = false) {
  const video = el('camera-preview');
  if (camera?.getVideoTracks().some((track) => track.readyState === 'live')) {
    const current = cameraGeneration;
    video.srcObject = camera; await video.play();
    return current === cameraGeneration && !exiting && (!requireConsent || el('camera-consent').checked);
  }
  stopCamera(false);
  const current = cameraGeneration;
  const allowed = () => current === cameraGeneration && !exiting && (!requireConsent || el('camera-consent').checked);
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: false,
      video: { width: { ideal: 640 }, height: { ideal: 360 }, frameRate: { ideal: 24, max: 30 } } });
  } catch (error) { if (!allowed()) return false; throw error; }
  if (!allowed()) { stream.getTracks().forEach((track) => track.stop()); return false; }
  camera = stream; video.srcObject = stream;
  try { await video.play(); }
  catch (error) { if (!allowed()) return false; stopCamera(false); throw error; }
  if (!allowed()) { stream.getTracks().forEach((track) => track.stop()); return false; }
  return true;
}
function enqueue(path, body) {
  // Compact metadata only. One request at a time; retries retain identity and sequence.
  if (queuedUpdates >= 20) { message('Monitoring delivery backlog; observations may be missing.'); return Promise.resolve({ delivered: false }); }
  queuedUpdates += 1;
  const delivery = deliveryGeneration;
  sendQueue = sendQueue.catch(() => {}).then(async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      if (delivery !== deliveryGeneration) return { delivered: false };
      try { const result = await request(path, 'POST', body); return { delivered: result.saved === true }; }
      catch (error) { if (attempt === 2) el('monitoring-status').textContent = `Monitoring delivery unconfirmed: ${error.message}`; }
    }
    return { delivered: false };
  }).finally(() => { queuedUpdates -= 1; });
  return sendQueue;
}
function sendEvent(event) { return enqueue('/camera', { ...event, interview_id: id, event_id: crypto.randomUUID(), sequence: ++sequence }); }
function heartbeat(status, run) {
  const body = { status, sequence: ++heartbeatSequence, ...(run ? { run } : {}) };
  return exiting ? request('/heartbeat', 'POST', body, true).catch(() => {}) : enqueue('/heartbeat', body);
}
async function flushMonitoring(reason = 'connection_ended') {
  cameraCallSid = null;
  const delivery = stopCamera(true, reason);
  let timer;
  try { await Promise.race([Promise.all([delivery, sendQueue]), new Promise((resolve) => { timer = setTimeout(resolve, 3000); })]); }
  finally { clearTimeout(timer); deliveryGeneration += 1; }
}
async function refresh() {
  info = await request();
  sequence = Math.max(sequence, info.camera_sequence); heartbeatSequence = Math.max(heartbeatSequence, info.heartbeat_sequence);
  el('interview-title').textContent = info.title;
  el('phone-monitoring-info').textContent = info.camera.phone_enabled
    ? 'This interview also enables local mobile-phone detection. Possible sightings are sent for human review and never affect technical scores.'
    : 'Mobile-phone detection is not enabled for this interview.';
  el('interview-info').textContent = `${info.duration_minutes} minutes · ${info.language} · ${info.status} · ${Math.ceil(info.remaining_secs / 60)} active minutes remaining`;
  const terminal = ['completed', 'failed', 'abandoned'].includes(info.status);
  el('start').disabled = terminal || !prepared || running || info.status === 'in_progress';
  el('start').textContent = info.status === 'paused' ? 'Resume interview' : 'Start interview';
  el('ready-interview').disabled = terminal || running;
  if (terminal) { stopCheck(); stopCamera(false); message(`Interview ${info.status}. You may close this page.`); }
}
el('check-mic').addEventListener('click', async () => {
  if (running) return;
  stopCheck();
  try {
    checkStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true }, video: false });
    audioContext = new AudioContext(); await audioContext.resume();
    const analyser = audioContext.createAnalyser(); analyser.fftSize = 256;
    audioContext.createMediaStreamSource(checkStream).connect(analyser);
    const buffer = new Uint8Array(analyser.fftSize);
    meterTimer = setInterval(() => { analyser.getByteTimeDomainData(buffer); el('check-level').value = Math.min(1, Math.sqrt(buffer.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / buffer.length) * 4); }, 100);
    micChecked = true; message('Microphone available. Speak and check the meter; then confirm the speaker test.');
  } catch (error) { micChecked = false; message(`Microphone unavailable: ${error.message}. Allow access in browser settings and retry.`); stopCheck(); }
});
el('check-speaker').addEventListener('click', async () => {
  const context = new AudioContext();
  try {
    await context.resume(); const oscillator = context.createOscillator(); const gain = context.createGain();
    oscillator.frequency.value = 440; gain.gain.value = .08; oscillator.connect(gain).connect(context.destination);
    oscillator.start(); oscillator.stop(context.currentTime + .4); oscillator.onended = () => void context.close();
  } catch (error) { await context.close(); message(`Speaker test failed: ${error.message}`); }
});
el('preview-camera').addEventListener('click', async () => {
  if (running) return;
  try {
    if (await openCamera()) el('monitoring-status').textContent = 'Local preview only. Analysis starts with the interview if you consent.';
  } catch (error) { message(`Camera unavailable: ${error.message}. Voice participation remains available.`); stopCamera(); }
});
el('ready-interview').addEventListener('click', async () => {
  if (!micChecked || !el('speaker-confirmed').checked) { message('Check your microphone and confirm that you heard the speaker tone.'); return; }
  try {
    await request('/prepare', 'POST', { phase: 'ready', camera_consent: el('camera-consent').checked });
    prepared = true; stopCheck(); await refresh(); message('Ready. Start the interview when you are comfortable.');
  } catch (error) { message(error.message); }
});
el('camera-consent').addEventListener('change', async () => {
  if (running && el('camera-consent').checked) { el('camera-consent').checked = false; message('Pause and prepare devices again to enable monitoring.'); return; }
  if (!el('camera-consent').checked) {
    await flushMonitoring('consent_withdrawn');
    await request('/revoke-camera-consent', 'POST').catch((error) => message(error.message));
  }
  if (!running) { prepared = false; el('start').disabled = true; }
});
globalThis.interviewParticipation = {
  sessionId: id,
  createTransport: () => new CandidateTransport({ iceServers: [] }),
  headers: signalingHeaders, offerEndpoint: `${base}/offer`, endEndpoint: `${base}/end`,
  beforeStart: async () => {
    if (!prepared || running) { message('Complete device preparation first.'); return false; }
    deliveryGeneration += 1; exiting = false;
    running = true;
    await refresh();
    if (['completed', 'failed', 'abandoned', 'in_progress'].includes(info.status)) { running = false; message(`Cannot start: interview is ${info.status}. Pause the previous connection before resuming.`); return false; }
    if (el('camera-consent').checked) {
      message('Opening the camera for this interview connection…');
      try {
        if (await openCamera(true)) el('monitoring-status').textContent = 'Camera ready. Local analysis starts when the interview connects.';
      } catch (error) {
        stopCamera(false);
        el('monitoring-status').textContent = `Camera unavailable: ${error.message}. Voice can continue; camera coverage will be unknown.`;
      }
    } else stopCamera(false);
    if (!running || exiting) return false;
    await request('/prepare', 'POST', { phase: 'ready', camera_consent: el('camera-consent').checked });
    // SmallWebRTC's existing DailyMediaManager reuses this instance. Only its audio
    // device helper is used; our separate camera stream is never passed to it.
    const mediaCall = Daily.getCallInstance() || Daily.createCallObject({ dailyConfig: { avoidEval: true } });
    message('Loading the audio device helper…');
    // Load before DailyMediaManager supplies its own startCamera dailyConfig;
    // otherwise that object would replace avoidEval before the first bundle load.
    await mediaCall.load({ dailyConfig: { avoidEval: true } });
    stopCheck(); el('start').disabled = true; el('ready-interview').disabled = true; return true;
  },
  onSession: async (call_sid) => {
    cameraCallSid = call_sid;
    try {
      const current = await request();
      if (!running || cameraCallSid !== call_sid) return;
      if (!current.monitoring_run || current.monitoring_run.call_sid !== call_sid) throw new Error('Camera run is unavailable.');
      monitor = new CameraMonitor({ video: el('camera-preview'), config: current.camera, elapsed: current.elapsed_secs,
        callSid: call_sid, run: current.monitoring_run, send: sendEvent, heartbeat,
        status: (text) => { el('monitoring-status').textContent = text; } });
      await monitor.start(camera, el('camera-consent').checked);
    } catch { stopCamera(false); el('monitoring-status').textContent = 'Camera analysis unavailable; voice can continue. Coverage will be unknown.'; }
  },
  beforeEnd: () => flushMonitoring(),
  onEnd: async () => { running = false; cameraCallSid = null; stopCheck(); stopCamera(false); try { await refresh(); if (info.status === 'paused') message('Paused. Resume will reopen the camera if monitoring consent is still checked. Your accepted answers are saved.'); } catch (error) { message(error.message); } },
  onPageHide: () => { exiting = true; cameraCallSid = null; stopCheck(); void stopCamera(true, 'page_hidden'); },
};
el('abandon').addEventListener('click', async () => {
  if (!confirm('End this interview permanently? You will not be able to resume.')) return;
  try {
    el('end').click();
    await request('/abandon', 'POST'); running = false; prepared = false; stopCheck(); stopCamera(false); await refresh();
  } catch (error) { message(error.message); }
});
try {
  await refresh();
  if (!['completed', 'failed', 'abandoned', 'in_progress'].includes(info.status)) await request('/prepare', 'POST', { phase: 'device_check', camera_consent: false });
  await import('./demo.js');
} catch (error) { message(`Cannot load this interview: ${error.message}`); }
