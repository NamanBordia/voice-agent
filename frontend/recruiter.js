import { renderInterviewReport } from './interview-report.js';

const el = (id) => document.getElementById(id);
let definition = null;
let reportId = null;
let reportLoading = false;
let reviewSaving = false;
const reportQuery = new URLSearchParams(location.search || '');
const reportMode = reportQuery.has('report');
const reportSections = ['report-summary', 'report-transcript', 'report-camera', 'report-issues', 'human-review', 'report-diagnostics'];
let sessions = [];
let planBusy = false;
let planEdited = false;
let savedDefinitions = [];
// Keep a newly issued link visible when switching selections in this page only.
const candidateLinks = new Map();
const status = (message, tone = 'info') => { el('workspace-status').textContent = message; el('workspace-status').dataset.tone = tone; };
async function request(path, method = 'GET', body) {
  if (globalThis.workspaceRequest) return globalThis.workspaceRequest(path, method, body);
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail));
  return result;
}
function button(text, action) {
  const node = document.createElement('button'); node.textContent = text; node.className = 'secondary';
  node.type = 'button';
  node.addEventListener('click', () => Promise.resolve().then(action).catch((error) => status(error.message, 'error')));
  return node;
}
function activeSession() {
  return definition && sessions.find((session) => session.definition_id === definition.id);
}
function updatePlanControls() {
  const activated = Boolean(activeSession());
  el('review-plan').disabled = planBusy || !definition || activated;
  el('activate-plan').disabled = planBusy || !definition || activated || !definition.reviewed || planEdited;
  el('copy-plan').disabled = planBusy || !definition;
  el('plan-editor').readOnly = planBusy || !definition || activated;
  el('share-status').textContent = activated ? 'This interview is activated. Use its original link or copy the plan into a new interview.'
    : !definition ? 'Select or draft an interview, then validate its reviewed plan.'
      : planEdited ? 'Unsaved plan changes. Validate and save from Questions & rubric before activation.'
        : definition.reviewed ? 'The reviewed plan is saved. You can activate a private candidate link.'
          : 'Draft created. Review the questions and validate the plan before activation.';
}
function node(tag, text = '', className = '') {
  const value = document.createElement(tag); value.textContent = text; value.className = className; return value;
}
function showStep(step, focus = false) {
  el('interview-builder').hidden = false;
  for (const name of ['role', 'questions', 'share']) {
    el(`builder-${name}`).hidden = name !== step;
    if (name === step) el(`step-${name}`).setAttribute('aria-current', 'step');
    else el(`step-${name}`).removeAttribute('aria-current');
  }
  if (focus) { el('interview-builder').scrollIntoView({ behavior: 'smooth', block: 'start' }); el('builder-heading').focus({ preventScroll: true }); }
}
function previewPlan() {
  el('question-cards').replaceChildren(); el('plan-outline').replaceChildren();
  if (!definition) { el('plan-preview-status').textContent = 'Draft or select an interview to see its plan.'; return; }
  let plan;
  try {
    plan = JSON.parse(el('plan-editor').value);
    if (!plan || !Array.isArray(plan.sections) || plan.sections.some((section) => !section || typeof section !== 'object'
      || !Array.isArray(section.questions) || section.questions.some((question) => !question || typeof question !== 'object'))) throw new Error();
  }
  catch { el('plan-preview-status').textContent = 'The JSON cannot be previewed yet. Fix it in Advanced, then validate and save. Your text is retained.'; return; }
  el('plan-preview-status').textContent = planEdited ? 'Preview of unsaved JSON. Save and validate before activation.' : 'Read-only question preview. Use Advanced to edit the complete plan.';
  el('outline-title').textContent = definition.job.title;
  el('outline-meta').textContent = `${definition.job.duration_minutes} minutes · ${definition.job.language} · ${plan.sections.length} sections`;
  for (const section of plan.sections) {
    el('plan-outline').append(node('li', `${section.title || section.id || 'Section'}${Number.isFinite(section.seconds) ? ` · ${section.seconds}s` : ''}`));
    for (const question of Array.isArray(section.questions) ? section.questions : []) {
      const card = node('article', '', 'question-card');
      card.append(node('p', `${section.title || section.id || 'Section'} · ${question.id || 'Missing ID'}`, 'eyebrow'));
      card.append(node('p', question.text || 'Question text missing', 'question-text'));
      const meta = node('div', '', 'question-meta');
      if (Number.isFinite(section.seconds)) meta.append(node('span', `${section.seconds}s section budget`));
      if (Number.isFinite(question.max_followups)) meta.append(node('span', `Up to ${question.max_followups} follow-ups`));
      meta.append(node('span', section.kind === 'candidate_questions' ? 'Closing · not scored' : 'Technical question'));
      card.append(meta);
      const skills = node('p');
      for (const skill of Array.isArray(question.skills) ? question.skills : []) skills.append(node('span', skill, 'chip'));
      card.append(skills, node('h3', 'Reviewed criteria'));
      const criteria = node('ul'); for (const item of Array.isArray(question.criteria) ? question.criteria : []) criteria.append(node('li', item));
      card.append(criteria); el('question-cards').append(card);
    }
  }
}
function showCandidateLink() {
  const url = definition && candidateLinks.get(definition.id);
  if (url) {
    const link = document.createElement('a'); link.href = url; link.textContent = new URL(url, location.origin).href;
    el('candidate-link').replaceChildren('Save this participation link before leaving this page. ', link);
  } else {
    el('candidate-link').replaceChildren(activeSession()
      ? 'Already activated. Use the original candidate link, or Copy into new interview to create a fresh link. The original token cannot be retrieved.'
      : '');
  }
}
function select(value) {
  definition = value;
  planEdited = false;
  el('plan-role').textContent = `${value.job.title} · ${value.job.language} · ${value.job.duration_minutes} minutes`;
  el('plan-editor').value = JSON.stringify(value.plan, null, 2);
  el('builder-heading').textContent = value.job.title;
  previewPlan(); showStep('questions', true);
  updatePlanControls(); showCandidateLink();
  if (activeSession()) status('This plan is already activated and cannot be edited or activated again. Use the original link or copy it into a new interview.');
}
async function refresh() {
  status('Loading saved interviews…', 'loading');
  let data;
  try { data = await request('/api/interviews'); }
  catch (error) {
    if (!savedDefinitions.length) {
      const row = node('tr'); const cell = node('td', 'Saved interviews could not be loaded. Use Refresh list to retry.', 'empty'); cell.colSpan = 5;
      row.append(cell); el('interview-list').replaceChildren(row); el('list-count').textContent = 'List unavailable';
    }
    throw error;
  }
  sessions = data.sessions;
  savedDefinitions = data.definitions;
  renderList();
  updatePlanControls(); showCandidateLink(); status('');
}
function renderList() {
  const search = (el('interview-search').value || '').trim().toLocaleLowerCase();
  el('interview-list').replaceChildren();
  let shown = 0;
  for (const value of savedDefinitions) {
    const related = sessions.filter((session) => session.definition_id === value.id);
    const phase = related.length ? 'activated' : value.reviewed ? 'reviewed' : 'draft';
    const skills = Array.isArray(value.job.skills) ? value.job.skills : [];
    if (!`${value.job.title} ${skills.join(' ')} ${value.job.language} ${phase} ${related.map((s) => s.status).join(' ')}`.toLocaleLowerCase().includes(search)) continue;
    shown += 1;
    const row = node('tr'); const role = node('td');
    const choose = button(value.job.title, () => { if (!planBusy) select(value); }); choose.className = 'role-button';
    role.append(choose, node('p', value.job.language, 'hint'));
    const skillCell = node('td');
    for (const skill of skills) skillCell.append(node('span', skill, 'chip'));
    if (!skills.length) skillCell.textContent = 'No skills recorded';
    const state = node('td'); state.append(node('span', phase, `badge ${phase === 'draft' ? 'neutral' : 'ok'}`));
    for (const session of related) state.append(node('p', session.status, 'hint'));
    const actions = node('td'); const group = node('div', '', 'row-actions');
    group.append(button('Open plan', () => { if (!planBusy) select(value); }));
    for (const session of related) {
      const link = node('a', 'Open report ↗', 'report-open-link');
      const url = new URL('/interviews', location.origin); url.searchParams.set('report', session.id);
      link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `Open report for ${value.job.title} (${session.status}) in a new tab`);
      group.append(link);
    }
    actions.append(group); row.append(role, skillCell, node('td', `${value.job.duration_minutes} min`), state, actions);
    el('interview-list').append(row);
  }
  el('list-count').textContent = `${shown} of ${savedDefinitions.length} interviews`;
  if (!shown) {
    const row = node('tr'); const cell = node('td', search ? 'No matching interviews. Try another role or skill.' : 'No interviews yet. Create your first interview to get started.', 'empty');
    cell.colSpan = 5; row.append(cell); el('interview-list').append(row);
  }
}
function block(title, text) {
  const section = document.createElement('details');
  const heading = document.createElement('summary'); heading.textContent = title;
  const content = document.createElement('pre'); content.textContent = text;
  section.append(heading, content); el('report-content').append(section);
}
async function showReport(id) {
  if (reportLoading) return;
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(id || '')) throw new Error('Invalid report link. Open a report from the interview list.');
  reportLoading = true; el('reload-report').disabled = true;
  try {
    status('Loading interview report…', 'loading');
    const report = await request(`/api/interviews/sessions/${encodeURIComponent(id)}/report`); reportId = id;
    el('report-panel').hidden = false;
    el('report-heading').textContent = report.role || 'Interview report'; document.title = `${report.role || 'Interview'} — Report · Voice Agent`;
    el('report-overview').textContent = `${report.language.toUpperCase()} · Interview evidence and observations`;
    el('report-state').textContent = report.status.replaceAll('_', ' ');
    el('report-content').replaceChildren();
    block('Coverage and uncertainty', JSON.stringify({ topics: report.topics, skills: report.skills, uncertainty: report.uncertainty }, null, 2));
    const technicalIds = new Set(report.reviewed_plan.sections.filter((section) => section.kind === 'technical').flatMap((section) => section.questions.map((question) => question.id)));
    const technicalTurns = report.answers.filter((answer) => technicalIds.has(answer.question_id) && answer.technical_issue);
    if (technicalTurns.length) block('Assessments needing human review',
      'These transcripts were retained. Technical interruptions or discarded evidence can leave an assessment incomplete; this is separate from a candidate providing insufficient evidence.\n\n' +
      JSON.stringify(technicalTurns.map((answer) => ({ turn: answer.event_id, question: answer.question_id,
        issue: answer.technical_issue, retained_criteria: answer.evidence.map((item) => item.criterion) })), null, 2));
    block('Defined rubric', JSON.stringify(report.rubric, null, 2));
    block('Reviewed question criteria and missing evidence', JSON.stringify({ plan: report.reviewed_plan, missing_evidence: report.missing_evidence }, null, 2));
    for (const answer of report.answers) block(`Turn ${answer.event_id} · ${answer.kind} · ${answer.question_id}`, `${answer.prompt}\n\nCandidate: ${answer.text}\n\n${JSON.stringify({ evidence: answer.evidence, technical_issue: answer.technical_issue, action: answer.action }, null, 2)}`);
    block('Questions and follow-ups', JSON.stringify(report.questions, null, 2));
    block('Technical and possible transcription issues', JSON.stringify(report.technical_issues, null, 2));
    const monitoringNote = report.monitoring.status === 'unavailable'
      ? 'Monitoring was unavailable or updates stopped arriving. Its observations are incomplete.'
      : report.monitoring.status === 'not_started'
        ? 'No analysis was confirmed for this session.'
        : 'This is the latest reported monitoring status; it does not establish continuous coverage.';
    const runs = report.monitoring.runs || [];
    if (runs.length) {
      const totals = {};
      const intervals = runs.filter((run) => Number.isFinite(run.start_secs) && Number.isFinite(run.observed_until_secs))
        .map((run) => [Math.max(0, run.start_secs), Math.min(report.duration_secs, run.observed_until_secs)])
        .filter(([start, end]) => end >= start).sort((a, b) => a[0] - b[0]);
      let accounted = 0; let previousEnd = 0;
      for (const [start, end] of intervals) { accounted += Math.max(0, end - Math.max(start, previousEnd)); previousEnd = Math.max(previousEnd, end); }
      totals.interview_active_secs = report.duration_secs;
      totals.time_outside_monitoring_runs_secs = Math.max(0, report.duration_secs - accounted);
      for (const name of ['face', 'phone']) {
        const enabled = runs.filter((run) => run[name].enabled);
        const first = enabled.map((run) => run[name].first_analyzed_secs).filter((value) => value !== null);
        const last = enabled.map((run) => run[name].last_analyzed_secs).filter((value) => value !== null);
        totals[name] = { enabled_runs: enabled.length, reported_frames_analyzed: enabled.reduce((sum, run) => sum + run[name].frames_analyzed, 0),
          reported_covered_secs: enabled.reduce((sum, run) => sum + run[name].covered_secs, 0),
          unknown_or_unavailable_secs: enabled.reduce((sum, run) => sum + run[name].unknown_secs, 0),
          first_analyzed_secs: first.length ? Math.min(...first) : null, last_analyzed_secs: last.length ? Math.max(...last) : null };
      }
      block('Video analysis coverage', `Browser-reported metadata, not independent verification. Unknown time is never counted as successful coverage. Time outside monitoring runs is also unverified. Disabled detectors have no analysis claim.\n\n${JSON.stringify(totals, null, 2)}\n\nRuns and interruptions:\n${JSON.stringify(runs, null, 2)}`);
    } else block('Video analysis coverage', 'Unknown. This record has no detector-frame or coverage summaries; an empty event list is inconclusive.');
    const phones = report.camera_events.filter((event) => event.type === 'phone_detected');
    block('Possible phone detections — human review', `These are possible visible-object sightings, not evidence of phone use or misconduct. Model scores are not calibrated probabilities. Duration describes the observation span; an isolated missed sample may occur within it.\n\n${JSON.stringify(phones.map((event) => ({ start_secs: event.start_secs, duration_secs: event.duration_secs, model_score: event.confidence })), null, 2)}`);
    block('Separate camera timeline', `${report.camera_notice}\n\n${monitoringNote}\n${report.camera_events.length ? '' : 'No camera events were saved. This alone does not confirm that detection worked throughout.'}\n\n${JSON.stringify({ monitoring: report.monitoring, events: report.camera_events }, null, 2)}`);
    renderInterviewReport(report, el);
    el('recruiter-notes').value = report.recruiter_notes; el('human-decision').value = report.human_decision;
    el('save-review').disabled = false; el('print-report').disabled = false;
    showReportSection(); status('');
  } finally { reportLoading = false; el('reload-report').disabled = false; }
}
function showReportSection(hash = location.hash || '') {
  const answer = /^#answer-\d+$/.test(hash) ? hash.slice(1) : null;
  const section = answer ? 'report-transcript' : reportSections.includes(hash.slice(1)) ? hash.slice(1) : 'report-summary';
  for (const name of reportSections) {
    el(name).hidden = name !== section;
    if (name === section) el(`nav-${name}`).setAttribute('aria-current', 'page');
    else el(`nav-${name}`).removeAttribute('aria-current');
  }
  if (answer) { el(answer)?.scrollIntoView({ block: 'center' }); el(answer)?.focus({ preventScroll: true }); }
}
el('job-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (planBusy) return;
  planBusy = true; updatePlanControls();
  try {
    const form = new FormData(event.target); const job = Object.fromEntries(form);
    for (const key of ['skills', 'topics', 'criteria']) job[key] = job[key].split('\n').map((x) => x.trim()).filter(Boolean);
    job.duration_minutes = Number(job.duration_minutes); job.camera = {};
    job.camera.phone_enabled = form.has('phone_enabled'); delete job.phone_enabled;
    for (const key of ['fps', 'confidence', 'absent_secs', 'multiple_secs', 'displacement_secs', 'displacement_ratio', 'cooldown_secs', 'phone_fps', 'phone_confidence', 'phone_sustain_secs', 'phone_cooldown_secs']) { job.camera[key] = Number(job[key]); delete job[key]; }
    select(await request('/api/interviews', 'POST', job)); await refresh(); status('Draft created. Review question wording, skill mappings, and rubrics.');
  } catch (error) { status(error.message, 'error'); }
  finally { planBusy = false; updatePlanControls(); }
});
el('plan-editor').addEventListener('input', () => { planEdited = true; updatePlanControls(); previewPlan(); });
el('review-plan').addEventListener('click', async () => {
  if (planBusy || !definition || activeSession()) return;
  planBusy = true; updatePlanControls();
  try { select(await request(`/api/interviews/${definition.id}/plan`, 'PUT', JSON.parse(el('plan-editor').value))); await refresh(); showStep('share'); status('Reviewed plan saved.'); }
  catch (error) { await refresh().catch(() => {}); status(error.message, 'error'); }
  finally { planBusy = false; updatePlanControls(); }
});
el('activate-plan').addEventListener('click', async () => {
  if (planBusy || !definition || activeSession() || !definition.reviewed || planEdited) return;
  planBusy = true; updatePlanControls();
  try {
    const data = await request(`/api/interviews/${definition.id}/activate`, 'POST');
    candidateLinks.set(definition.id, data.candidate_url);
    sessions.push({ id: data.session_id, definition_id: definition.id, status: 'created' });
    updatePlanControls(); showCandidateLink();
    await refresh(); status('Activated. Save the candidate link before leaving this page.');
  } catch (error) { await refresh().catch(() => {}); status(error.message, 'error'); }
  finally { planBusy = false; updatePlanControls(); }
});
el('copy-plan').addEventListener('click', async () => {
  if (planBusy || !definition) return;
  planBusy = true; updatePlanControls();
  try {
    const plan = JSON.parse(el('plan-editor').value);
    const copy = await request('/api/interviews', 'POST', definition.job);
    select({ ...copy, plan });
    await refresh(); status('Copied into a new draft. Validate and save the reviewed plan, then activate to generate a fresh candidate link.');
  } catch (error) { status(error.message, 'error'); }
  finally { planBusy = false; updatePlanControls(); }
});
el('create-interview').addEventListener('click', () => { el('builder-heading').textContent = 'Create an interview'; showStep('role', true); });
el('close-builder').addEventListener('click', () => { el('interview-builder').hidden = true; el('create-interview').focus(); });
for (const step of ['role', 'questions', 'share']) el(`step-${step}`).addEventListener('click', () => showStep(step));
el('continue-share').addEventListener('click', () => showStep('share'));
el('interview-search').addEventListener('input', renderList);
el('refresh').addEventListener('click', () => refresh().catch((e) => status(e.message, 'error')));
el('save-review').addEventListener('click', async () => {
  if (!reportId || reviewSaving) return;
  reviewSaving = true; el('save-review').disabled = true; el('review-status').textContent = 'Saving review…';
  try { await request(`/api/interviews/sessions/${reportId}/review`, 'PUT', { recruiter_notes: el('recruiter-notes').value, human_decision: el('human-decision').value }); el('review-status').textContent = 'Human review saved.'; }
  catch (error) { el('review-status').textContent = error.message; }
  finally { reviewSaving = false; el('save-review').disabled = false; }
});
el('interview-dashboard').hidden = reportMode;
el('report-page').hidden = !reportMode;
document.body.classList.toggle('report-page', reportMode);
if (reportMode) {
  const load = () => showReport(reportQuery.get('report')).catch((error) => {
    if (!reportId) el('report-heading').textContent = 'Report unavailable'; status(error.message, 'error');
  });
  for (const name of reportSections) el(`nav-${name}`).addEventListener('click', () => showReportSection(`#${name}`));
  globalThis.addEventListener?.('hashchange', () => showReportSection());
  el('reload-report').addEventListener('click', load);
  el('print-report').addEventListener('click', () => globalThis.print());
  void load();
} else refresh().catch((error) => status(error.message, 'error'));
