// Read-only presentation of saved assessments. No provider calls or answer re-scoring.
const rank = { insufficient_evidence: 0, developing: 1, meets: 2, strong: 3 };
const labels = { insufficient_evidence: 'Insufficient evidence', developing: 'Developing', meets: 'Meets criteria', strong: 'Strong' };
const blocked = /Decision fallback: no assessment recorded|Possible transcription ambiguity/i;
const countText = (count, singular, plural = `${singular}s`) => `${count} ${count === 1 ? singular : plural}`;
const nonnegative = (value) => Number.isFinite(value) ? Math.max(0, value) : 0;
const clock = (seconds) => `${Math.floor(nonnegative(seconds) / 60)}:${String(Math.floor(nonnegative(seconds) % 60)).padStart(2, '0')}`;

function sectionSummary(section, report) {
  const questions = section.questions || [];
  const ids = new Set(questions.map((question) => question.id));
  const turns = (report.answers || []).filter((answer) => ids.has(answer.question_id));
  if (section.kind === 'candidate_questions') {
    return { title: section.title, rating: 'Not scored', tone: 'neutral',
      explanation: turns.length ? 'Candidate questions and closing are recorded separately from technical performance.'
        : 'No candidate-question response was recorded.', criteria: [], reviewCount: 0 };
  }
  const criteria = [];
  for (const question of questions) {
    for (const criterion of new Set(question.criteria || [])) {
      let best = null;
      for (const answer of turns) {
        if (answer.question_id !== question.id || answer.kind !== 'answer' || blocked.test(answer.technical_issue || '')) continue;
        const matches = (answer.evidence || []).filter((item) => item.criterion === criterion);
        if (matches.length !== 1) continue;
        const item = matches[0];
        if (!Object.hasOwn(rank, item.rating)) continue;
        if (item.excerpt && !(answer.text || '').includes(item.excerpt)) continue;
        if (rank[item.rating] > 0 && !item.excerpt) continue;
        if (!best || rank[item.rating] >= rank[best.rating]) best = { ...item, turn: answer.event_id };
      }
      criteria.push({ criterion, question: question.id, rating: best?.rating || 'not_assessed',
        explanation: best?.justification || 'No usable assessment was recorded for this criterion.',
        excerpt: best?.excerpt || '', turn: best?.turn ?? null });
    }
  }
  const counts = { insufficient_evidence: 0, developing: 0, meets: 0, strong: 0, not_assessed: 0 };
  for (const item of criteria) counts[item.rating] += 1;
  const assessed = criteria.length - counts.not_assessed;
  let rating = 'Not assessed'; let tone = 'neutral';
  if (assessed) {
    tone = 'warn';
    if (counts.not_assessed) rating = 'Partial assessment';
    else if (counts.insufficient_evidence === assessed) rating = 'Insufficient evidence';
    else if (counts.insufficient_evidence) rating = 'Mixed evidence';
    else if (counts.developing) rating = 'Developing';
    else { rating = counts.strong === assessed ? 'Strong' : 'Meets reviewed criteria'; tone = 'ok'; }
  }
  const descriptions = [];
  if (counts.meets + counts.strong) descriptions.push(`${countText(counts.meets + counts.strong, 'criterion met', 'criteria met')}`);
  if (counts.developing) descriptions.push(`${countText(counts.developing, 'criterion still developing', 'criteria still developing')}`);
  if (counts.insufficient_evidence) descriptions.push(`${countText(counts.insufficient_evidence, 'criterion needs more evidence', 'criteria need more evidence')}`);
  if (counts.not_assessed) descriptions.push(`${countText(counts.not_assessed, 'criterion not assessed', 'criteria not assessed')}`);
  const asked = (report.questions || []).some((question) => ids.has(question.question_id));
  const explanation = descriptions.length ? `${descriptions.join('; ')}.`
    : 'No reviewed criteria were available to summarize.';
  return { title: section.title, rating, tone, criteria,
    explanation: !asked && !turns.length ? `This section was not reached. ${explanation}` : explanation,
    reviewCount: turns.filter((answer) => answer.technical_issue).length };
}

function detectorSummary(report, name) {
  const runs = report.monitoring?.runs || [];
  const enabled = runs.filter((run) => run[name]?.enabled);
  const frames = enabled.reduce((total, run) => total + nonnegative(run[name].frames_analyzed), 0);
  const duration = nonnegative(report.duration_secs);
  const covered = Math.min(duration, enabled.reduce((total, run) => total + nonnegative(run[name].covered_secs), 0));
  const coverage = `${Math.round(covered)} of ${Math.round(duration)} active seconds had browser-reported analysis coverage.`;
  return { enabled: enabled.length, frames, covered, duration, coverage, hasRuns: runs.length > 0 };
}

function phoneSummary(report) {
  const events = (report.camera_events || []).filter((event) => event.type === 'phone_detected');
  const detector = detectorSummary(report, 'phone');
  if (events.length) return { title: 'Mobile phone: possible sighting recorded', tone: 'warn',
    explanation: `${countText(events.length, 'possible phone sighting')} saved. These observations do not establish phone use or misconduct.`,
    coverage: detector.hasRuns ? detector.coverage : 'Analysis coverage is unknown for this older record.',
    sightings: events.slice(0, 5).map((event) => `At ${clock(event.start_secs)}, spanning ${nonnegative(event.duration_secs).toFixed(1)} seconds.`),
    more: Math.max(0, events.length - 5) };
  if (!detector.hasRuns) return { title: 'Mobile phone: result unknown', tone: 'neutral',
    explanation: 'No phone sighting was saved, and this record has no phone-analysis coverage history.', coverage: '', sightings: [], more: 0 };
  if (!detector.enabled) return { title: 'Mobile phone: monitoring did not run', tone: 'neutral',
    explanation: 'Phone analysis was not enabled for any run, or monitoring consent was not provided. Phone presence cannot be determined.',
    coverage: '', sightings: [], more: 0 };
  if (!detector.frames) return { title: 'Mobile phone: detection unavailable', tone: 'neutral',
    explanation: 'Phone monitoring was enabled, but no successfully analyzed frames were reported. Phone presence cannot be determined.',
    coverage: detector.coverage, sightings: [], more: 0 };
  return { title: 'Mobile phone: no sighting recorded', tone: 'neutral',
    explanation: `The detector processed ${countText(detector.frames, 'frame')}, but no sustained phone sighting was saved. This does not prove that no phone was present.`,
    coverage: detector.coverage, sightings: [], more: 0 };
}

function faceSummary(report) {
  const detector = detectorSummary(report, 'face');
  if (!detector.hasRuns) return 'Face monitoring coverage is unknown for this older record.';
  if (!detector.enabled) return 'Face monitoring did not run, so face presence cannot be determined.';
  if (!detector.frames) return 'Face monitoring was enabled, but no successfully analyzed frames were reported.';
  const events = report.camera_events || [];
  const absent = events.filter((event) => event.type === 'face_absent').length;
  const multiple = events.filter((event) => event.type === 'multiple_faces_detected').length;
  const moved = events.filter((event) => event.type === 'face_position_changed').length;
  const observations = [];
  if (absent) observations.push(countText(absent, 'observation with no face detected', 'observations with no face detected'));
  if (multiple) observations.push(countText(multiple, 'observation with multiple faces', 'observations with multiple faces'));
  if (moved) observations.push(countText(moved, 'sustained face-position change'));
  return `Face monitoring processed ${countText(detector.frames, 'frame')}. ${detector.coverage} ${observations.length
    ? `The browser reported ${observations.join(', ')}.` : 'No face-related events were saved; that alone does not verify continuous face presence.'}`;
}

function issueNotes(issues) {
  const notes = new Set();
  for (const issue of issues || []) {
    if (/credits exhausted/i.test(issue)) notes.add('The speech provider ran out of credits.');
    else if (/timed out|timeout/i.test(issue)) notes.add('A provider request or connection timed out.');
    else if (/Connection ended before completion/i.test(issue)) notes.add('An earlier connection ended before the interview finished.');
    else if (/omitted spoken text/i.test(issue)) notes.add('An interviewer reply was missing and a fallback prompt was used.');
    else if (/Spoken response repaired/i.test(issue)) notes.add('An invalid interviewer follow-up was replaced.');
    else if (/evidence omitted|evidence discarded/i.test(issue)) notes.add('Some assessment evidence was discarded and needs human review.');
    else if (/incomplete|transcription/i.test(issue)) notes.add('Some speech may have been incomplete or mistranscribed.');
    else notes.add('Other technical issues were recorded; check the detailed report.');
  }
  return [...notes];
}

/** Summarize saved rubric evidence and monitoring metadata without changing the report. */
export function summarizeReport(report) {
  const states = { completed: 'Completed', paused: 'Paused', abandoned: 'Ended before completion',
    in_progress: 'In progress', failed: 'Failed', created: 'Not started', ready: 'Ready', device_check: 'Device check' };
  return {
    overview: `${report.role || 'Interview'} — ${states[report.status] || 'Status unknown'}. Active interview time: ${clock(report.duration_secs)}.`,
    sections: (report.reviewed_plan?.sections || []).map((section) => sectionSummary(section, report)),
    phone: phoneSummary(report), face: faceSummary(report),
    issues: issueNotes([...(report.technical_issues || []), ...(report.answers || []).map((answer) => answer.technical_issue).filter(Boolean)]),
  };
}

function node(tag, text, className = '') {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

/** Render a plain-English summary using text nodes, below the original detailed report. */
export function renderReportSummary(report, target) {
  const summary = summarizeReport(report);
  target.replaceChildren();
  target.append(node('h2', 'Plain-English interview summary'), node('p', summary.overview));
  target.append(node('p', 'Ratings use the strongest supported assessment for each reviewed criterion across answers and follow-ups. A section meets criteria only when every criterion has supporting evidence. Missing assessments are not treated as failures.', 'muted'));
  const table = node('table');
  const header = node('tr');
  for (const label of ['Section', 'Rating', 'Summary']) { const cell = node('th', label); cell.scope = 'col'; header.append(cell); }
  const head = node('thead'); head.append(header); table.append(head);
  const body = node('tbody');
  for (const section of summary.sections) {
    const row = node('tr'); const title = node('th', section.title); title.scope = 'row';
    const rating = node('td'); rating.append(node('span', section.rating, `badge ${section.tone}`));
    const explanation = node('td'); explanation.append(node('p', section.explanation));
    if (section.reviewCount) explanation.append(node('p', `${countText(section.reviewCount, 'turn has', 'turns have')} review notes in the detailed report.`, 'muted'));
    if (section.criteria.length) {
      const details = node('details'); details.append(node('summary', 'Criterion ratings and reasons'));
      const list = node('ul');
      for (const item of section.criteria) {
        const label = labels[item.rating] || 'Not assessed';
        list.append(node('li', `${item.criterion}: ${label}. ${item.explanation}${item.turn !== null ? ` (Turn ${item.turn}.)` : ''}`));
      }
      details.append(list); explanation.append(details);
    }
    row.append(title, rating, explanation); body.append(row);
  }
  table.append(body);
  const wrapper = node('div', undefined, 'summary-table'); wrapper.append(table); target.append(wrapper);
  target.append(node('h3', summary.phone.title), node('p', summary.phone.explanation));
  if (summary.phone.coverage) target.append(node('p', summary.phone.coverage));
  if (summary.phone.sightings.length) {
    const list = node('ul'); for (const sighting of summary.phone.sightings) list.append(node('li', sighting));
    target.append(list);
    if (summary.phone.more) target.append(node('p', `${countText(summary.phone.more, 'additional sighting')} in the detailed timeline.`));
  }
  target.append(node('h3', 'Face monitoring'), node('p', summary.face));
  target.append(node('p', 'Camera observations are browser-reported and need human review. They do not affect the section ratings.', 'muted'));
  target.append(node('h3', 'Issues to review'));
  if (summary.issues.length) {
    const list = node('ul'); for (const issue of summary.issues) list.append(node('li', issue)); target.append(list);
  } else target.append(node('p', 'No technical issues were recorded. This does not independently verify transcription or assessment accuracy.'));
  target.append(node('p', 'Review the original answers and rubric before making a decision. No automatic hiring recommendation is generated.', 'muted'));
}
