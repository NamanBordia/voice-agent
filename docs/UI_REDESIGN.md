# Interview UI redesign

Implemented October 30, 2026 as a presentation-layer update to the existing Jinja/plain-JavaScript application.

## Visual changes

- Shared indigo/slate tokens, local system typography, light surfaces, 8px cards, visible keyboard focus and responsive navigation under the existing Voice Agent name.
- Recruiter sidebar and searchable saved-interview table using actual definitions and session statuses. No invented analytics.
- Three builder steps: Role details, Questions & rubric, and Review & share. Step changes retain the existing form and unsaved plan text. Navigation never submits or activates a plan.
- Readable question cards and an outline derived from the current JSON. Advanced JSON remains the authoritative editor and preserves stable IDs and additional fields on submission. Activated plans remain read-only.
- Light candidate device preparation, persistent local camera preview, unchecked consent, and accurate local-camera/cloud-voice wording.
- Dark navy interview view with the latest spoken interviewer text, actual connection/turn status, microphone-gating explanation, collapsible transcript, and the original controls. Device checks collapse once on entering the live layout and can be reopened.
- Readable transcript/evidence and monitoring sections in reports, plus expandable complete raw diagnostics. Camera findings remain separate from technical assessment. Existing rating aggregation is unchanged.
- Shared styling for Talk, Configuration and Calls/history; configuration controls have associated labels.

## Functional boundaries

Routes, bearer-token scope, recruiter access rules, backend models, persistence formats, providers, observers, fixed sample rate, question progression and assessment logic are unchanged. No dependencies or component library were added.

`frontend/candidate-view.js` is a read-only DOM presentation adapter. It observes existing state and transcript updates and does not fetch data, create media, invoke participation hooks, or recreate the video/audio nodes. Candidate view changes use CSS and native details elements. Existing handlers still own Start, Mute, Pause/Resume, permanent End, camera consent and cleanup.

`frontend/candidate.js` additionally exposes the already-fetched session status as a DOM data attribute and formats its existing metadata for display. Remaining time is labelled as the last server update; the UI does not invent a running countdown.

## Files

| Files | Changes |
|---|---|
| `app/templates/_base.html` | Shared shell, active navigation, candidate-only header and stylesheet links |
| `app/static/base.css` | Existing foundation CSS extracted unchanged from the base template |
| `app/static/studio.css` | Design tokens, shared surfaces, recruiter/builder/report/candidate layouts and responsive rules |
| `app/templates/interviews.html` | Saved table, guided builder, report sections and Advanced controls |
| `frontend/recruiter.js` | Client-side search, step navigation, safe read-only plan/report rendering and existing API bindings |
| `app/templates/candidate.html` | Device layout, camera/consent area, live prompt, transcript and existing controls |
| `frontend/candidate-view.js` | Read-only connection/prompt/gating presentation |
| `frontend/candidate.js` | Display-only session status and language/time text |
| `app/templates/config.html` | Labels associated with existing inputs; submitted names preserved |
| `app/templates/demo.html` | Description lists all five existing languages |
| `frontend/build.mjs` | Build the new candidate presentation bundle |
| `frontend/tests/recruiter.test.js` | Existing flows adapted to the table; JSON preservation, navigation, search and full form serialization checks |
| `frontend/tests/candidate-view.test.js` | Truthful presentation and stable media-node checks |
| `tests/test_ui_templates.py` | Unique bindings, complete form fields, candidate separation, unchecked consent and media ancestry checks |

## Verification

- Full frontend suite: 70 passing tests, including mocked media lifecycle, signaling, gating, delivery, reporting and the added UI cases.
- Targeted backend/template suite: 44 passing tests covering templates, dashboard, browser routes, interview service and startup. Existing `audioop` deprecation warning remains.
- Frontend production build completed successfully.
- Headless Chrome inspected the actual rendered Jinja templates and built bundles using an isolated local fixture server with mocked API responses. It checked recruiter search, builder/activation state, report readability, candidate consent/privacy, prompt/gating state and unchanged media-node identity.
- Page-level overflow checks passed at 320, 390, 768, 1024 and 1440px across the inspected layouts. Tables can scroll within their own containers.
- No JavaScript runtime exceptions or external network requests occurred during the browser inspection. No interview offer or paid provider request was made.
- Mocked screenshots are in ignored local `scratch/ui-review/`, including recruiter, builder, report, device preparation, live interview, Talk, Configuration and Calls.

Automated checks do not validate audible quality, actual camera recognition, real device permissions, live latency or provider recovery. A human browser interview remains required after the redesign.

## Deliberate presentation choices

The provided attachment contained the written visual brief but no mockup image files. Layout and colors follow that brief; exact image matching could not be checked.

Candidate section progress, examples and hints are omitted because the existing scoped candidate response does not expose a reviewed plan or those fields. The prompt is derived only from text already spoken to the candidate. No recruiter-only criteria are sent to support the design.

Device selection dropdowns are not added: the existing preparation UI uses browser-default devices. No waveform is fabricated; the existing microphone meter and actual status text remain.

Controls stay at the bottom of the conversation panel and use normal document flow so they cannot cover prompts, consent or errors. The permanent-end action keeps the existing browser confirmation and handler.

## Run and review

```powershell
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Hard-refresh existing tabs after the update. Review `/interviews`, `/`, `/config`, `/calls` and a private candidate link. Before a live demo, check drafting, invalid JSON, save, activation, copying, link retention, device preparation, consent withdrawal, voice-only participation, mute, pause/resume and permanent ending using the existing manual checks in [INTERVIEWS.md](INTERVIEWS.md).
