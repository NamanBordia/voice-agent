// Exercise the actual recruiter page with mocked DOM/HTTP, without providers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let moduleId = 0;
class Element {
  handlers = {};
  dataset = {};
  attributes = {};
  children = [];
  disabled = false;
  textContent = '';
  addEventListener(name, handler) { this.handlers[name] = handler; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  scrollIntoView() {}
  focus() {}
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const sourceDefinition = () => ({ id: 'original', reviewed: true,
  job: { title: 'API developer', language: 'en', duration_minutes: 20 },
  plan: { sections: [{ questions: [{ text: 'A custom reviewed question?' }] }] } });

async function page({ activated = true, failActivation = false, gateActivation = null, report = null, formEntries = [] } = {}) {
  const original = sourceDefinition();
  const definitions = [structuredClone(original)];
  const sessions = activated ? [{ id: 'existing-session', definition_id: original.id, status: 'paused' }] : [];
  const requests = [];
  const elements = {};
  const el = (id) => elements[id] ||= new Element();
  globalThis.document = { getElementById: el, createElement: () => new Element() };
  globalThis.location = { origin: 'http://localhost:8000' };
  globalThis.FormData = class extends Map { constructor() { super(formEntries); } };
  globalThis.fetch = async (path, options) => {
    const method = options.method; const body = options.body && JSON.parse(options.body);
    requests.push({ path, method, body });
    if (path === '/api/interviews/sessions/existing-session/report') return Response.json(report);
    if (path === '/api/interviews' && method === 'GET') return Response.json({ definitions, sessions });
    if (path === '/api/interviews' && method === 'POST') {
      const copy = { id: 'copy', reviewed: false, job: body, plan: { sections: [] } };
      definitions.push(copy); return Response.json(copy);
    }
    if (path === '/api/interviews/copy/plan' && method === 'PUT') {
      definitions[1].plan = body; definitions[1].reviewed = true; return Response.json(definitions[1]);
    }
    if (path === '/api/interviews/original/plan' && method === 'PUT') {
      definitions[0].plan = body; definitions[0].reviewed = true; return Response.json(definitions[0]);
    }
    if (path.endsWith('/activate') && method === 'POST') {
      if (gateActivation) await gateActivation;
      const identity = path.split('/')[3];
      sessions.push({ id: 'new-session', definition_id: identity, status: 'created' });
      if (failActivation) return Response.json({ detail: 'Already activated; retain the original candidate link' }, { status: 409 });
      return Response.json({ session_id: 'new-session', candidate_url: '/candidate/new-session#token=test-token' });
    }
    assert.fail(`Unexpected request: ${method} ${path}`);
  };
  const source = (await readFile(new URL('../recruiter.js', import.meta.url), 'utf8'))
    .replace("'./report-summary.js'", JSON.stringify(new URL('../report-summary.js', import.meta.url).href));
  await import(`data:text/javascript;base64,${Buffer.from(`${source}\n// case ${moduleId++}`).toString('base64')}`);
  await settle();
  const select = async (index = 0) => { await elements['interview-list'].children[index].children[0].children[0].handlers.click(); };
  return { elements, requests, definitions, sessions, original, select };
}

test('previously activated interviews show their status and cannot send review/activation requests', async () => {
  const { elements, requests, select } = await page();
  assert.match(elements['interview-list'].children[0].children[3].children[0].textContent, /activated/);
  await select();
  assert.equal(elements['review-plan'].disabled, true);
  assert.equal(elements['activate-plan'].disabled, true);
  assert.equal(elements['plan-editor'].readOnly, true);
  assert.equal(elements['copy-plan'].disabled, false);
  assert.match(elements['candidate-link'].children[0], /original candidate link/);
  await elements['review-plan'].handlers.click();
  await elements['activate-plan'].handlers.click();
  assert.equal(requests.filter((request) => request.method !== 'GET').length, 0);
});

test('copying an activated plan requires review, then creates a fresh link while preserving the original', async () => {
  const { elements, requests, definitions, sessions, original, select } = await page();
  await select(); await elements['copy-plan'].handlers.click();
  assert.deepEqual(JSON.parse(elements['plan-editor'].value), original.plan);
  assert.equal(elements['plan-editor'].readOnly, false);
  assert.equal(elements['review-plan'].disabled, false);
  assert.equal(elements['activate-plan'].disabled, true);
  await elements['activate-plan'].handlers.click();
  assert.equal(requests.some((request) => request.path.endsWith('/activate')), false);
  await elements['review-plan'].handlers.click();
  assert.equal(elements['activate-plan'].disabled, false);
  await elements['activate-plan'].handlers.click();
  const link = elements['candidate-link'].children[1];
  assert.equal(link.href, '/candidate/new-session#token=test-token');
  assert.equal(elements['review-plan'].disabled, true);
  assert.equal(elements['activate-plan'].disabled, true);
  assert.deepEqual(definitions[0], original);
  assert.deepEqual(sessions[0], { id: 'existing-session', definition_id: 'original', status: 'paused' });
  assert.deepEqual(requests.filter((request) => request.method !== 'GET').map(({ path }) => path),
    ['/api/interviews', '/api/interviews/copy/plan', '/api/interviews/copy/activate']);
  await select(0); await select(1);
  assert.equal(elements['candidate-link'].children[1].href, link.href);
  await elements.refresh.handlers.click();
  assert.equal(elements['candidate-link'].children[1].href, link.href);
});

test('activation reserves the button immediately and ignores double clicks', async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const { elements, requests, select } = await page({ activated: false, gateActivation: gate });
  await select();
  const first = elements['activate-plan'].handlers.click();
  assert.equal(elements['activate-plan'].disabled, true);
  await elements['activate-plan'].handlers.click();
  assert.equal(requests.filter((request) => request.path.endsWith('/activate')).length, 1);
  release(); await first;
  assert.equal(elements['activate-plan'].disabled, true);
  assert.match(elements['candidate-link'].children[1].href, /#token=/);
});

test('a conflict from another tab refreshes activation state and prevents repeated conflicts', async () => {
  const { elements, requests, select } = await page({ activated: false, failActivation: true });
  await select(); await elements['activate-plan'].handlers.click();
  assert.equal(elements['activate-plan'].disabled, true);
  assert.equal(elements['review-plan'].disabled, true);
  assert.match(elements['workspace-status'].textContent, /Already activated/);
  await elements['activate-plan'].handlers.click();
  assert.equal(requests.filter((request) => request.path.endsWith('/activate')).length, 1);
});

test('recruiter report distinguishes technical omissions from weak answers and empty camera events', async () => {
  const report = { role: 'API developer', language: 'en', status: 'completed', duration_secs: 200,
    topics: [], skills: [{ skill: 'API', status: 'not_assessed' }], uncertainty: 'Assessment interrupted.',
    reviewed_plan: { sections: [{ kind: 'technical', questions: [{ id: 'q_1' }] }] },
    missing_evidence: ['q_1: Reasoning'], answers: [{ event_id: 2, kind: 'answer', question_id: 'q_1',
      prompt: 'Describe a task', text: 'An API answer', evidence: [], technical_issue: 'Assessment evidence omitted: ungrounded excerpt', action: 'clarify' }],
    questions: [], technical_issues: [], camera_notice: 'Unverified observations',
    monitoring: { status: 'unavailable' }, camera_events: [], recruiter_notes: '', human_decision: '' };
  const { elements } = await page({ report });
  await elements['interview-list'].children[0].children[4].children[0].children[1].handlers.click();
  const sections = elements['report-content'].children;
  const interruptions = sections.find((section) => section.children[0].textContent === 'Assessments needing human review');
  assert.match(interruptions.children[1].textContent, /separate from a candidate providing insufficient evidence/);
  assert.match(interruptions.children[1].textContent, /"turn": 2/);
  const camera = sections.find((section) => section.children[0].textContent === 'Separate camera timeline');
  assert.match(camera.children[1].textContent, /Monitoring was unavailable/);
  assert.match(camera.children[1].textContent, /does not confirm that detection worked throughout/);
});

test('report totals count cumulative runs once and label phone events for human review', async () => {
  const metrics = { enabled: true, status: 'stopped', frames_analyzed: 4, first_analyzed_secs: .25, last_analyzed_secs: 1,
    covered_secs: .75, unknown_secs: 1.25, gaps: [], last_error: null };
  const report = { role: 'API developer', language: 'en', status: 'completed', duration_secs: 5,
    topics: [], skills: [], uncertainty: '', reviewed_plan: { sections: [] }, missing_evidence: [], answers: [],
    questions: [], technical_issues: [], camera_notice: 'Unverified', monitoring: { status: 'stopped', runs: [
      { call_sid: 'c1', start_secs: 0, observed_until_secs: 2, face: metrics, phone: metrics },
      { call_sid: 'c2', start_secs: 3, observed_until_secs: 5, face: metrics, phone: metrics }] },
    camera_events: [{ type: 'phone_detected', start_secs: 1, duration_secs: 2, confidence: .85 }], recruiter_notes: '', human_decision: '' };
  const { elements } = await page({ report });
  await elements['interview-list'].children[0].children[4].children[0].children[1].handlers.click();
  const sections = elements['report-content'].children;
  const coverage = sections.find((section) => section.children[0].textContent === 'Video analysis coverage').children[1].textContent;
  assert.match(coverage, /"reported_frames_analyzed": 8/);
  assert.match(coverage, /"unknown_or_unavailable_secs": 2.5/);
  assert.match(coverage, /"time_outside_monitoring_runs_secs": 1/);
  const phones = sections.find((section) => section.children[0].textContent === 'Possible phone detections — human review').children[1].textContent;
  assert.match(phones, /not evidence of phone use or misconduct/);
  assert.match(phones, /"model_score": 0.85/);
  const plainText = (node) => [node.textContent || '', ...(node.children || []).map(plainText)].join(' ');
  const summary = plainText(elements['report-summary']);
  assert.match(summary, /Plain-English interview summary/);
  assert.match(summary, /Mobile phone: possible sighting recorded/);
  assert.match(summary, /At 0:01, spanning 2.0 seconds/);
});

test('builder step navigation and closing preserve unsaved JSON, stable IDs and additional fields', async () => {
  const p = await page({ activated: false }); await p.select();
  const plan = { sections: [{ id: 'stable-section', seconds: 120, kind: 'technical', extra_metadata: { keep: true },
    questions: [{ id: 'stable-question', text: '<script>Not HTML</script>', skills: ['API'], criteria: ['Reasoning'], max_followups: 2 }] }] };
  p.elements['plan-editor'].value = JSON.stringify(plan);
  p.elements['plan-editor'].handlers.input();
  for (const step of ['role', 'share', 'questions']) await p.elements[`step-${step}`].handlers.click();
  await p.elements['close-builder'].handlers.click(); await p.elements['create-interview'].handlers.click();
  assert.deepEqual(JSON.parse(p.elements['plan-editor'].value), plan);
  assert.equal(p.elements['activate-plan'].disabled, true);
  assert.equal(p.requests.filter((r) => r.method !== 'GET').length, 0);
  await p.elements['review-plan'].handlers.click();
  const save = p.requests.find((r) => r.method === 'PUT');
  assert.deepEqual(save.body, plan);
  assert.equal(p.elements['builder-share'].hidden, false);
  assert.equal(p.elements['activate-plan'].disabled, false);
});

test('invalid JSON stays editable and cannot activate or issue a review request', async () => {
  const p = await page({ activated: false }); await p.select();
  p.elements['plan-editor'].value = '{ unfinished'; p.elements['plan-editor'].handlers.input();
  assert.match(p.elements['plan-preview-status'].textContent, /cannot be previewed/);
  await p.elements['review-plan'].handlers.click(); await p.elements['activate-plan'].handlers.click();
  assert.equal(p.elements['plan-editor'].value, '{ unfinished');
  assert.equal(p.elements['activate-plan'].disabled, true);
  assert.equal(p.elements['workspace-status'].dataset.tone, 'error');
  assert.equal(p.requests.filter((r) => r.method !== 'GET').length, 0);
});

test('search filters local saved data and has an honest empty state without API writes', async () => {
  const p = await page();
  p.elements['interview-search'].value = 'paused'; p.elements['interview-search'].handlers.input();
  assert.match(p.elements['list-count'].textContent, /1 of 1/);
  p.elements['interview-search'].value = 'no such role'; p.elements['interview-search'].handlers.input();
  assert.match(p.elements['interview-list'].children[0].children[0].textContent, /No matching/);
  assert.equal(p.requests.filter((r) => r.method !== 'GET').length, 0);
});

test('role form retains every original field and serializes all camera settings with existing types', async () => {
  const settings = { fps: '4', confidence: '.6', absent_secs: '3', multiple_secs: '2', displacement_secs: '3',
    displacement_ratio: '.25', cooldown_secs: '15', phone_fps: '2', phone_confidence: '.35', phone_sustain_secs: '2', phone_cooldown_secs: '15' };
  const p = await page({ activated: false, formEntries: Object.entries({ title: 'Backend developer', description: 'Build APIs',
    skills: 'Python\nAPI\n', topics: 'API development\n', criteria: 'Reasoning\nCorrectness', experience: 'Entry level',
    duration_minutes: '20', language: 'kn', resume: 'Candidate context', phone_enabled: 'on', ...settings }) });
  await p.elements['job-form'].handlers.submit({ preventDefault() {}, target: {} });
  const created = p.requests.find((r) => r.method === 'POST');
  assert.equal(created.path, '/api/interviews');
  assert.deepEqual(created.body.skills, ['Python', 'API']);
  assert.deepEqual(created.body.topics, ['API development']);
  assert.equal(created.body.duration_minutes, 20); assert.equal(created.body.language, 'kn');
  assert.equal(created.body.resume, 'Candidate context'); assert.equal(created.body.camera.phone_enabled, true);
  for (const [key, value] of Object.entries(settings)) assert.equal(created.body.camera[key], Number(value));
});
