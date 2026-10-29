import test from 'node:test';
import assert from 'node:assert/strict';
import { summarizeReport, renderReportSummary } from '../report-summary.js';

const criteria = ['Understands the problem', 'Explains the algorithm'];
function answer(eventId, ratings, { kind = 'answer', issue = '' } = {}) {
  return { event_id: eventId, question_id: 'q_1', kind, text: 'I use a hash map to find the missing price.',
    technical_issue: issue, evidence: ratings.map((rating, index) => ({ criterion: criteria[index], rating,
      excerpt: rating === 'insufficient_evidence' || rating === 'not_assessed' ? '' : 'hash map',
      justification: rating === 'insufficient_evidence' ? 'No explanation was supplied.' : 'Describes a lookup.' })) };
}
function report(answers = []) {
  return { role: 'Backend Intern', status: 'completed', duration_secs: 155,
    reviewed_plan: { sections: [
      { title: 'Arrays and hash maps', kind: 'technical', questions: [{ id: 'q_1', criteria }] },
      { title: 'Candidate questions', kind: 'candidate_questions', questions: [{ id: 'closing', criteria: ['No technical rating'] }] },
    ] },
    questions: [{ question_id: 'q_1' }], answers, technical_issues: [], camera_events: [], monitoring: { runs: [] } };
}
function run(name, { frames = 10, covered = 100, enabled = true } = {}) {
  return { call_sid: name, start_secs: 0, observed_until_secs: 155,
    face: { enabled, frames_analyzed: frames, covered_secs: covered },
    phone: { enabled, frames_analyzed: frames, covered_secs: covered } };
}

test('section ratings combine the strongest supported criteria across follow-ups without averaging down later focused answers', () => {
  const value = report([answer(2, ['insufficient_evidence', 'insufficient_evidence']),
    answer(4, ['meets', 'developing']), answer(6, ['insufficient_evidence', 'meets'])]);
  const before = structuredClone(value);
  const summary = summarizeReport(value);
  assert.equal(summary.sections[0].rating, 'Meets reviewed criteria');
  assert.deepEqual(summary.sections[0].criteria.map((item) => item.turn), [4, 6]);
  assert.match(summary.sections[0].explanation, /2 criteria met/);
  assert.deepEqual(value, before);
});

test('missing assessments stay separate from insufficient evidence and mixed/developing/strong performance', () => {
  const cases = [
    [[], 'Not assessed'],
    [[answer(2, ['not_assessed', 'not_assessed'])], 'Not assessed'],
    [[answer(2, ['meets'])], 'Partial assessment'],
    [[answer(2, ['insufficient_evidence', 'insufficient_evidence'])], 'Insufficient evidence'],
    [[answer(2, ['meets', 'insufficient_evidence'])], 'Mixed evidence'],
    [[answer(2, ['strong', 'developing'])], 'Developing'],
    [[answer(2, ['strong', 'strong'])], 'Strong'],
  ];
  for (const [answers, expected] of cases) assert.equal(summarizeReport(report(answers)).sections[0].rating, expected);
});

test('unverified quotes, duplicate criteria, requests and technical failures cannot establish a section rating', () => {
  const variants = [
    answer(2, ['meets', 'strong'], { kind: 'clarification_request' }),
    answer(2, ['meets', 'strong'], { issue: 'Decision fallback: no assessment recorded (decision_schema)' }),
    answer(2, ['meets', 'strong'], { issue: 'Possible transcription ambiguity' }),
    { ...answer(2, ['meets', 'strong']), question_id: 'some_other_question' },
  ];
  for (const item of variants) assert.equal(summarizeReport(report([item])).sections[0].rating, 'Not assessed');
  const bad = answer(2, ['meets', 'strong']);
  bad.evidence[0].excerpt = 'invented quote'; bad.evidence[1].excerpt = '';
  assert.equal(summarizeReport(report([bad])).sections[0].rating, 'Not assessed');
  const duplicate = answer(2, ['meets']); duplicate.evidence.push({ ...duplicate.evidence[0] });
  assert.equal(summarizeReport(report([duplicate])).sections[0].rating, 'Not assessed');
});

test('retained valid evidence is usable after speech repair or partial evidence omission, with review notes', () => {
  const value = report([answer(2, ['meets'], { issue: 'Spoken response repaired: multiple questions' }),
    answer(4, ['not_assessed', 'developing'], { issue: 'Assessment evidence omitted: ungrounded excerpt' })]);
  const summary = summarizeReport(value);
  assert.equal(summary.sections[0].rating, 'Developing');
  assert.equal(summary.sections[0].reviewCount, 2);
});

test('unreached sections are not failures and closing never receives a technical rating', () => {
  const value = report(); value.questions = [];
  value.answers.push({ ...answer(2, ['strong', 'strong']), question_id: 'closing' });
  const summary = summarizeReport(value);
  assert.equal(summary.sections[0].rating, 'Not assessed');
  assert.match(summary.sections[0].explanation, /not reached/);
  assert.equal(summary.sections[1].rating, 'Not scored');
  assert.equal(summary.sections[1].criteria.length, 0);
});

test('phone summaries distinguish unknown, disabled, unavailable and no recorded sightings', () => {
  const value = report();
  assert.equal(summarizeReport(value).phone.title, 'Mobile phone: result unknown');
  value.monitoring.runs = [run('off', { enabled: false, frames: 0, covered: 0 })];
  assert.equal(summarizeReport(value).phone.title, 'Mobile phone: monitoring did not run');
  value.monitoring.runs = [run('failed', { frames: 0, covered: 0 })];
  assert.equal(summarizeReport(value).phone.title, 'Mobile phone: detection unavailable');
  value.monitoring.runs = [run('partial')];
  const phone = summarizeReport(value).phone;
  assert.equal(phone.title, 'Mobile phone: no sighting recorded');
  assert.match(phone.explanation, /does not prove that no phone was present/);
  assert.match(phone.coverage, /100 of 155 active seconds/);
});

test('possible phone sightings show timing with bounded detail and never change section ratings', () => {
  const value = report([answer(2, ['meets', 'developing'])]);
  const before = summarizeReport(value).sections;
  value.camera_events = Array.from({ length: 8 }, (_, index) => ({ type: 'phone_detected', start_secs: 65 + index * 4, duration_secs: 2.5 }));
  value.camera_events.push({ type: 'face_absent' });
  const summary = summarizeReport(value);
  assert.equal(summary.phone.title, 'Mobile phone: possible sighting recorded');
  assert.match(summary.phone.explanation, /8 possible phone sightings saved/);
  assert.match(summary.phone.sightings[0], /At 1:05, spanning 2.5 seconds/);
  assert.equal(summary.phone.sightings.length, 5); assert.equal(summary.phone.more, 3);
  assert.deepEqual(summary.sections, before);
});

test('face observations and technical issues are explained without inferring candidate traits', () => {
  const value = report(); value.monitoring.runs = [run('face')];
  value.camera_events = [{ type: 'face_absent' }, { type: 'multiple_faces_detected' }];
  value.technical_issues = ['tts_provider_error: Credits exhausted', 'Connection ended before completion',
    'Spoken response repaired: ungrounded follow-up', 'Spoken response repaired: multiple questions'];
  const summary = summarizeReport(value);
  assert.match(summary.face, /1 observation with no face detected/);
  assert.match(summary.face, /1 observation with multiple faces/);
  assert.equal(summary.issues.length, 3);
  assert.match(summary.issues.join(' '), /ran out of credits/);
  assert.match(summary.overview, /Completed.*2:35/);
});

test('rendering creates ordinary readable HTML and treats source strings as text', () => {
  const created = [];
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; created.push(this); }
    append(...items) { this.children.push(...items); }
    replaceChildren() { this.children = []; }
  }
  globalThis.document = { createElement: (tag) => new Element(tag) };
  const target = new Element('section');
  const value = report([answer(2, ['meets', 'developing'])]);
  value.role = '<img src=x onerror=alert(1)>';
  renderReportSummary(value, target);
  const text = created.map((element) => element.textContent).join(' ');
  assert.match(text, /Plain-English interview summary/);
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /Mobile phone: result unknown/);
  assert.equal(created.some((element) => element.tag === 'img'), false);
  assert.equal(created.some((element) => element.tag === 'pre'), false);
  assert.equal(created.filter((element) => element.tag === 'table').length, 1);
  renderReportSummary({ ...value, role: 'Another interview' }, target);
  assert.equal(target.children.filter((element) => element.tag === 'h2').length, 1);
});
