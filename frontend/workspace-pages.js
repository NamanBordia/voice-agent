// Private content arrives only from the approved local API. Use text nodes for all data.
const node = (tag, text = '', className = '') => {
  const element = document.createElement(tag); element.textContent = text; element.className = className; return element;
};
const ms = (value) => Number.isFinite(value) ? `${Math.round(value)} ms` : '—';
function details(title, value) {
  const element = node('details'); element.append(node('summary', title), node('pre', JSON.stringify(value, null, 2))); return element;
}
function card(label, value, caption = '') {
  const element = node('div', '', 'card');
  element.append(node('div', label, 'lbl'), node('div', value, 'val'), node('p', caption, 'hint')); return element;
}

export async function setupConfig(request, root) {
  let config = await request('/api/workspace/config');
  const form = root.querySelector('form');
  const fields = [...form.querySelectorAll('[data-config-field]')];
  function populate() {
    for (const input of fields) {
      const [group, name] = input.dataset.configField.split('.');
      input.value = String(config[group][name]);
      if (name.endsWith('_frontend_origin')) input.disabled = true;
    }
  }
  populate();
  const status = node('p', 'Origin approvals are managed only in local Configuration.', 'hint');
  status.setAttribute('role', 'status'); form.append(status);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]'); button.disabled = true;
    const next = structuredClone(config);
    for (const input of fields.filter((field) => !field.disabled)) {
      const [group, name] = input.dataset.configField.split('.');
      // Pydantic owns strict ranges, enums and dependencies, including numeric input.
      next[group][name] = input.value;
    }
    try { config = await request('/api/workspace/config', 'PUT', next); populate(); status.textContent = 'Saved. Active interviews retain their settings snapshot.'; }
    catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  });
}

export async function showCalls(request, root) {
  const data = await request('/api/workspace/calls');
  root.replaceChildren(node('h1', 'Conversations'), node('p',
    'Responsiveness measures server time from turn release to first audio. Total includes endpointing. These are server timings, not browser-speaker playback.', 'hint'));
  const refresh = node('button', 'Refresh history', 'secondary'); refresh.type = 'button';
  refresh.addEventListener('click', async () => {
    refresh.disabled = true;
    try { await showCalls(request, root); }
    catch (error) { refresh.after(node('p', error.message, 'err')); refresh.disabled = false; }
  });
  root.append(refresh);
  if (!data.db_connected) root.append(node('p', 'History storage is unavailable.', 'err'));
  const panel = node('div', '', 'panel table-scroll'); const table = node('table');
  const head = node('thead'); const heading = node('tr');
  for (const label of ['Conversation', 'Status', 'Turns', 'Responsiveness', 'Total', 'Greeting', 'Created']) heading.append(node('th', label));
  head.append(heading); const body = node('tbody'); table.append(head, body);
  for (const log of data.logs) {
    const row = node('tr'); const id = node('td'); const link = node('a', log.call_sid);
    link.href = `/calls/${encodeURIComponent(log.call_sid)}`; id.append(link);
    row.append(id, node('td', log.conversation_outcome || '—'), node('td', `${log.turn_count} (${log.measured_latency_turns} timed)`),
      node('td', ms(log.resp_ms)), node('td', ms(log.total_ms)), node('td', log.metrics?.greeting_text_source || '—'), node('td', log.created_at));
    body.append(row);
  }
  if (!data.logs.length) { const row = node('tr'); const cell = node('td', data.db_connected ? 'No conversations yet. Open Talk to start.' : 'History storage is unavailable.', 'empty'); cell.colSpan = 7; row.append(cell); body.append(row); }
  panel.append(table); root.append(panel);
}

export async function showCall(request, root, id) {
  if (!/^browser_[a-f0-9]{32}$/.test(id)) throw new Error('Invalid conversation link.');
  const data = await request(`/api/workspace/calls/${id}`);
  const { log, latency, turn_records: turns } = data;
  root.replaceChildren(node('h1', `Conversation ${log.call_sid}`), node('p', `${log.created_at} · ${log.conversation_outcome || '—'}`, 'muted'));
  if (log.error) root.append(node('p', log.error, 'err'));
  const cards = node('div', '', 'cards');
  for (const [label, values] of [['Responsiveness', latency.responsiveness], ['Total', latency.total]]) {
    cards.append(card(`${label} · median`, ms(values.median), `avg ${ms(values.avg)} · p95 ${ms(values.p95)} · max ${ms(values.max)} · ${values.n} timed turns`));
  }
  cards.append(card('Turns', String(turns.length)), card('Greeting source', log.metrics?.greeting_text_source || '—')); root.append(cards);
  root.append(node('p', 'Responsiveness excludes endpointing and mid-thought pauses. Total includes endpointing. Both measure server events, not actual browser-speaker playback.', 'hint'));
  root.append(node('h2', 'Greeting'), node('pre', log.transcript.filter((event) => event.phase === 'greeting').map((event) => event.text).join('\n')));
  root.append(node('h2', 'Turns'));
  for (const turn of turns) {
    const section = node('article', '', 'panel answer-card');
    section.append(node('h3', `Turn ${turn.turn_id}`), node('h4', 'You'), node('p', turn.user_text || '—'), node('h4', 'Agent'), node('p', turn.assistant_text || '—'),
      node('p', `${turn.detected_language || '—'} → ${turn.response_language || '—'} · responsiveness ${ms(turn.latest_user_stop_to_first_audio_ms ?? turn.primary_latency_ms)} · total ${ms(turn.primary_latency_ms)}`, 'hint'));
    if (turn.error_stage) section.append(node('p', `${turn.error_stage}: ${turn.error_message || ''}`, 'err'));
    const pipeline = node('dl');
    for (const [label, field] of [['User to EOT', 'user_stop_to_eot_ms'], ['EOT to commit', 'eot_to_turn_committed_ms'], ['Commit to LLM', 'turn_committed_to_llm_start_ms'], ['LLM first token', 'llm_ttfb_ms'], ['LLM total', 'llm_total_ms'], ['LLM to TTS', 'llm_end_to_tts_start_ms'], ['TTS first audio', 'tts_first_audio_ms'], ['Memory processing', 'memory_processor_duration_ms']]) {
      pipeline.append(node('dt', label), node('dd', ms(turn[field])));
    }
    section.append(pipeline, details('Full turn metrics', turn)); root.append(section);
  }
  if (!turns.length) root.append(node('p', 'No timed turns were saved.', 'muted'));
  const transcript = node('section', '', 'panel'); transcript.append(node('h2', 'Full transcript'));
  for (const event of log.transcript) transcript.append(node('p', `${event.role || event.speaker || event.phase || 'Event'}: ${event.text}`));
  root.append(transcript);
  if (data.memory_summary) root.append(details('Memory summary', data.memory_summary));
  if (data.eager_summary) root.append(details('Historical eager / speculation summary', data.eager_summary));
  root.append(details('Saved configuration snapshot', log.config_snapshot), details('Complete saved record', log));
}
