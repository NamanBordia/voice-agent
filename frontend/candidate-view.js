// Read-only presentation adapter. Media and participation remain owned by the existing modules.
export function refreshCandidateView(doc = document) {
  const root = doc.getElementById('candidate-root');
  const conversation = doc.getElementById('conversation');
  const state = conversation.dataset.state;
  const active = !doc.getElementById('end').disabled;
  // Keep a started interview in its live layout across pause and recovery.
  if (active || ['connecting', 'preparing', 'speaking', 'listening', 'saving'].includes(state)) {
    if (root.dataset.mode !== 'live') {
      root.dataset.mode = 'live';
      doc.getElementById('device-settings').open = false;
    }
  }
  const session = root.dataset.sessionStatus;
  const connection = active ? `Connected · ${state === 'speaking' ? 'Interviewer speaking' : state === 'listening' ? 'Listening' : state === 'preparing' ? 'Processing' : 'Audio session active'}`
    : session === 'paused' ? 'Paused · Connection closed'
      : ['completed', 'failed', 'abandoned'].includes(session) ? `Interview ${session}`
        : state === 'connecting' || state === 'preparing' ? 'Connecting audio…'
          : state === 'saving' ? 'Closing and saving…' : 'Not connected';
  const connectionLabel = doc.getElementById('connection-state');
  if (connectionLabel.textContent !== connection) connectionLabel.textContent = connection;
  const prompts = doc.getElementById('transcript').querySelectorAll('.utterance--agent span');
  const latest = prompts[prompts.length - 1]?.textContent;
  if (latest && doc.getElementById('current-question').textContent !== latest) doc.getElementById('current-question').textContent = latest;
  const explanation = state === 'speaking'
    ? 'Interviewer speaking. Your microphone is temporarily gated; answer after the reply finishes.'
    : state === 'preparing' ? 'Processing the response. Your microphone is temporarily gated.'
      : state === 'listening' ? 'Your turn. The interviewer is listening.'
        : doc.getElementById('mute').getAttribute('aria-pressed') === 'true' ? 'You have muted your microphone.'
          : active ? 'The connection is active. Follow the interview status before speaking.'
            : 'No active interview audio connection. Prepare your devices, or resume when available.';
  const hint = doc.getElementById('gate-explanation');
  if (hint.textContent !== explanation) hint.textContent = explanation;
  const video = doc.getElementById('camera-preview');
  const live = video.srcObject?.getVideoTracks().some((track) => track.readyState === 'live');
  if (video.dataset.stream !== (live ? 'live' : 'none')) video.dataset.stream = live ? 'live' : 'none';
}

if (typeof document !== 'undefined' && typeof MutationObserver !== 'undefined') {
  const refresh = () => refreshCandidateView();
  const observer = new MutationObserver(refresh);
  observer.observe(document.getElementById('conversation'), { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: ['data-state', 'disabled', 'aria-pressed'] });
  observer.observe(document.getElementById('monitoring-status'), { childList: true, characterData: true, subtree: true });
  observer.observe(document.getElementById('candidate-root'), { attributes: true, attributeFilter: ['data-session-status'] });
  document.getElementById('camera-preview').addEventListener('loadeddata', refresh);
  window.addEventListener('pagehide', () => observer.disconnect(), { once: true });
  refresh();
}
