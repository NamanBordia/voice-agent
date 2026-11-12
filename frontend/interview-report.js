import { summarizeReport } from './report-summary.js';

const node = (tag, text = '', className = '') => {
  const element = document.createElement(tag); element.textContent = text; element.className = className; return element;
};
const clock = (value) => {
  const seconds = Number.isFinite(value) ? Math.max(0, value) : 0;
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
};
const ratings = { strong: 'Strong', meets: 'Meets criteria', developing: 'Developing',
  insufficient_evidence: 'Insufficient evidence', not_assessed: 'Not assessed' };
const tone = (rating) => ['strong', 'meets'].includes(rating) ? 'ok' : rating === 'not_assessed' ? 'neutral' : 'warn';
const badge = (label, style = 'neutral') => node('span', label, `badge ${style}`);
function card(title, description = '') {
  const value = node('article', '', 'report-card'); value.append(node('h2', title));
  if (description) value.append(node('p', description, 'muted')); return value;
}
function list(values) {
  const value = node('ul', '', 'report-list'); for (const text of values) value.append(node('li', text)); return value;
}
function phoneCard(summary) {
  const value = card('Mobile phone observations'); value.append(badge(summary.title.replace(/^Mobile phone: /, ''), summary.tone),
    node('p', summary.explanation), node('p', summary.coverage, 'muted'));
  if (summary.sightings.length) value.append(list(summary.sightings));
  if (summary.more) value.append(node('p', `${summary.more} more sightings in Camera observations.`, 'muted'));
  return value;
}

function overview(report, summary, target) {
  target.replaceChildren();
  target.append(node('h2', 'Section ratings'), node('p', 'Ratings reflect the strongest supported evidence for each reviewed criterion across answers and follow-ups. Missing assessments are not failures.', 'muted'));
  const sections = node('div', '', 'report-rating-grid');
  for (const section of summary.sections) {
    const value = card(section.title || 'Untitled section'); value.append(badge(section.rating, section.tone), node('p', section.explanation));
    if (section.reviewCount) value.append(node('p', `${section.reviewCount} response(s) have review notes.`, 'technical-note'));
    if (section.criteria.length) {
      const details = node('details'); details.append(node('summary', 'Criterion ratings and reasons'));
      for (const criterion of section.criteria) {
        const item = node('div', '', 'report-criterion'); item.append(node('h3', criterion.criterion),
          badge(ratings[criterion.rating] || 'Not assessed', tone(criterion.rating)), node('p', criterion.explanation));
        if (criterion.excerpt) item.append(node('blockquote', criterion.excerpt));
        if (criterion.turn !== null) {
          const link = node('a', `Read response ${criterion.turn}`); link.href = `#answer-${criterion.turn}`; item.append(link);
        }
        details.append(item);
      }
      value.append(details);
    }
    sections.append(value);
  }
  if (!summary.sections.length) sections.append(card('No reviewed sections', 'This record has no reviewed section ratings to display.'));
  target.append(sections);
  const observations = node('div', '', 'report-rating-grid'); observations.append(phoneCard(summary.phone), card('Face monitoring', summary.face));
  target.append(node('h2', 'Monitoring summary'), observations);
  const issues = card('Items to review');
  issues.append(summary.issues.length ? list(summary.issues) : node('p', 'No technical issues were recorded. This does not independently verify transcription or assessment accuracy.'));
  target.append(issues, node('p', 'Review the original answers and rubric before making a decision. Camera observations never change section ratings. No automatic hiring recommendation is generated.', 'report-footnote'));
}

function answers(report, target) {
  target.replaceChildren(node('h2', 'Answers & evidence'), node('p', 'Accepted responses in interview order, with the evidence recorded at that turn.', 'muted'));
  const closing = new Set((report.reviewed_plan?.sections || []).filter((section) => section.kind === 'candidate_questions').flatMap((section) => (section.questions || []).map((question) => question.id)));
  for (const answer of report.answers || []) {
    const value = node('article', '', 'report-card answer-card'); value.id = `answer-${answer.event_id}`; value.tabIndex = -1;
    value.append(node('p', `RESPONSE ${answer.event_id} · ${closing.has(answer.question_id) ? 'Candidate questions · not scored' : answer.kind.replaceAll('_', ' ')}`, 'eyebrow'),
      node('p', 'Interviewer', 'report-label'), node('h3', answer.prompt), node('p', 'Candidate', 'report-label'), node('p', answer.text, 'report-answer-text'));
    for (const evidence of answer.evidence || []) {
      const item = node('div', '', 'report-criterion'); item.append(node('strong', evidence.criterion),
        badge(ratings[evidence.rating] || 'Not assessed', tone(evidence.rating)), node('p', evidence.justification || 'No reason was recorded.'));
      if (evidence.excerpt) item.append(node('blockquote', evidence.excerpt)); value.append(item);
    }
    if (answer.technical_issue) value.append(node('p', answer.technical_issue, 'technical-note'));
    target.append(value);
  }
  if (!report.answers?.length) target.append(card('No accepted answers', 'This session has no accepted responses to display.'));
}

function coverage(report, name) {
  const runs = report.monitoring?.runs || []; const enabled = runs.filter((run) => run[name]?.enabled);
  const duration = Number.isFinite(report.duration_secs) ? Math.max(0, report.duration_secs) : 0;
  const total = (field) => enabled.reduce((sum, run) => sum + (Number.isFinite(run[name][field]) ? Math.max(0, run[name][field]) : 0), 0);
  const covered = Math.min(duration, total('covered_secs'));
  const value = card(name === 'face' ? 'Face analysis coverage' : 'Phone analysis coverage');
  if (!runs.length) value.append(node('p', 'Coverage unknown for this older record.'));
  else if (!enabled.length) value.append(node('p', 'Analysis did not run. Presence cannot be determined.'));
  else {
    value.append(node('p', `${Math.round(covered)} / ${Math.round(duration)} seconds`, 'report-coverage-value'));
    const progress = node('progress'); progress.max = duration || 1; progress.value = covered;
    progress.setAttribute('aria-label', `${name} browser-reported analysis coverage`);
    value.append(progress, node('p', `${total('frames_analyzed')} frames analyzed · ${Math.round(duration - covered)} seconds unverified`, 'muted'));
  }
  return value;
}

function camera(report, summary, target) {
  target.replaceChildren(node('h2', 'Camera observations'), node('p', report.camera_notice || 'Camera observations require human review.', 'report-footnote'));
  const metrics = node('div', '', 'report-rating-grid'); metrics.append(coverage(report, 'phone'), coverage(report, 'face')); target.append(metrics);
  target.append(phoneCard(summary.phone), card('Face monitoring', summary.face));
  const events = report.camera_events || [];
  const timeline = card('Observation timeline', 'Times use active interview time. Observation spans do not establish uninterrupted visibility.');
  if (events.length) {
    const table = node('table'); table.setAttribute('aria-label', 'Saved camera observations');
    const head = node('thead'); const headings = node('tr');
    for (const title of ['Time', 'Observation', 'Span', 'Faces', 'Model score']) { const th = node('th', title); th.scope = 'col'; headings.append(th); }
    head.append(headings); const body = node('tbody');
    const names = { phone_detected: 'Possible phone sighting', multiple_faces_detected: 'Multiple faces observed',
      face_absent: 'No face detected', face_position_changed: 'Face position changed', camera_unavailable: 'Camera unavailable', monitoring_unavailable: 'Monitoring unavailable' };
    for (const event of events) {
      const row = node('tr');
      for (const text of [clock(event.start_secs), names[event.type] || event.type.replaceAll('_', ' '), `${Number(event.duration_secs).toFixed(1)}s`,
        Number.isInteger(event.face_count) ? String(event.face_count) : '—', Number.isFinite(event.confidence) ? event.confidence.toFixed(2) : '—']) row.append(node('td', text));
      body.append(row);
    }
    table.append(head, body); const scroll = node('div', '', 'table-scroll'); scroll.append(table); timeline.append(scroll);
  } else timeline.append(node('p', 'No camera events were saved. This does not establish that no phone was present or that monitoring was continuous.'));
  timeline.append(node('p', 'Model scores are not calibrated probabilities. Sightings do not establish phone use or misconduct.', 'muted')); target.append(timeline);
}

/** Present existing saved evidence without modifying ratings or making provider calls. */
export function renderInterviewReport(report, getElement) {
  const summary = summarizeReport(report);
  const highlights = getElement('report-highlights'); highlights.replaceChildren();
  const criteria = summary.sections.reduce((total, section) => total + section.criteria.length, 0);
  for (const [label, value, detail] of [['Active time', clock(report.duration_secs), 'Excludes paused time'],
    ['Responses', String(report.answers?.length || 0), 'Accepted interview turns'], ['Reviewed criteria', String(criteria), 'Across technical sections'],
    ['Technical notes', String(summary.issues.length), 'Recorded technical concerns']]) {
    const metric = node('div', '', 'report-metric'); metric.append(node('p', label, 'report-label'), node('p', value, 'report-metric-value'), node('p', detail, 'muted')); highlights.append(metric);
  }
  overview(report, summary, getElement('report-summary'));
  answers(report, getElement('report-transcript'));
  camera(report, summary, getElement('report-camera'));
  const issues = getElement('report-issues'); issues.replaceChildren(node('h2', 'Issues & uncertainty'));
  const notes = card('Recorded technical issues'); notes.append(summary.issues.length ? list(summary.issues)
    : node('p', 'No technical issues were recorded. This does not independently verify transcription or assessment accuracy.'));
  const original = [...new Set([...(report.technical_issues || []), ...(report.answers || []).map((answer) => answer.technical_issue).filter(Boolean)])];
  if (original.length) { const details = node('details'); details.append(node('summary', 'Original issue notes'), list(original)); notes.append(details); }
  issues.append(notes, card('Assessment uncertainty', report.uncertainty), node('p', 'Only a human reviewer records a final decision.', 'report-footnote'));
}
