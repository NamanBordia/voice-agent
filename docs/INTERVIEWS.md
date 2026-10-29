# Single-interview workflow

The existing general demo remains at `/`. Interview mode shares its browser audio
client, Sarvam STT/TTS, Cerebras provider, fixed 16 kHz transport, memory processors,
no-barge-in gates, readiness handshake, observers, and once-only call finalizer.
Run one Uvicorn worker. There is one active audio runner across both modes.

## Setup and start (PowerShell)

Keep the existing virtual environment and local `.env`; no new Python dependency
or credential is needed. Use Node 22.14+ for the build.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run prepare:vision
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

`prepare:vision` downloads official BlazeFace short-range float16 **version 1** at
setup time and checks SHA-256
`b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f`.
Build copies WASM from exactly `@mediapipe/tasks-vision@0.10.32`. The model and
generated WASM/JS are ignored by Git and must be prepared on another checkout.
Vision runtime uses same-origin assets only. Missing model/unsupported worker capabilities
produce visible `monitoring_unavailable`; voice participation remains available.

`prepare:vision` also verifies the approved EfficientDet-Lite0 **int8 version 1**
phone model (`efficientdet_lite0.tflite`), SHA-256
`0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb`.
Its embedded category metadata includes `cell phone`. Both model files are local
generated assets and must be prepared on another checkout.

Open [Interview workspace](http://localhost:8000/interviews). Enter role details,
skills, topics, criteria, experience, duration, language, and optional pasted resume.
Drafting uses free localized templates quoting your topics; it makes no provider
request. Review **every question**, difficulty, language, budget, criterion, and skill
mapping in the JSON editor. Assign relevant supplied skills to questions explicitly;
unmapped/uncovered skills appear as **not assessed**. Save the reviewed plan, then
activate. Activated definitions are immutable; create another interview to change one.
New drafts ask one focused implementation question per topic. The decision prompt
requests follow-ups about implementation, operation, and reasoning on separate turns
within the reviewed budget and experience level. Existing activated wording is retained.

Saved interviews show **activated** when a candidate session already exists. Their
review and activation buttons stay disabled. To reuse that job and question plan,
select it and choose **Copy into new interview**, validate/save the copied plan,
then activate the new draft. This creates a separate session with a fresh link;
the original interview and its answers remain available in its report.

Retain the candidate link when shown: its random participation token is issued once
and only its SHA-256 hash is stored. Enter through that link in another tab. The
workspace keeps newly issued links visible while switching selections on the same
page, but cannot retrieve an old token after a reload. If the original link is lost,
use the copy workflow above to create a new interview.
The fragment token is moved to tab-scoped sessionStorage and removed from the visible
URL. Keep the original link to reopen participation in a different tab/device.
The preparation page checks microphone availability and level, plays a local speaker
tone for human confirmation, and offers local camera preview and explicit consent.
Analysis starts only with interview participation and consent. Consent can be
withdrawn during the interview. Camera denial/unavailability does not block voice.

After readiness the configured language introduction and reviewed first question
are spoken. Answer after the interviewer stops. **Pause** drains media, checkpoints,
and closes camera resources; **Resume** establishes new media and explicitly repeats
the pending question. With monitoring consent still checked, Start/Resume acquires
a fresh local camera stream before connecting (or reuses a live preview). Another
Preview click is unnecessary. If access is denied, voice can continue and monitoring
records unavailable/unknown coverage. Withdrawn consent prevents reacquisition;
late permission results after page exit are released without reopening the preview. **End
interview permanently** marks an unfinished session abandoned and prevents resume.
Normal progression or the duration deadline closes the interview automatically.

Candidate media failures pause through the scoped end endpoint even if no session
announcement has arrived yet. Candidate transport does not automatically retry an
offer against a reserved session, or send ICE without a server peer ID. Wait for
cleanup, then use **Resume interview**. Close old candidate tabs after updating the
frontend so their previously scheduled SDK retries cannot reserve another runner.

## Controller and speech boundary

If a decision requires clarification or a follow-up but omits spoken text, the
controller repeats the current prompt and records a technical issue. It retains
the accepted answer and enforces the existing time/request/follow-up limits.
Wrong, unrelated, very short, and don't-know answers are normal turns: the decision
prompt asks for a relevant clarification, a polite redirect, or reviewed progression.
Before strict controller validation, evidence is checked separately from speech.
Case/whitespace differences in criterion names and excerpts can be resolved only
to reviewed criteria and exact substrings of the accepted transcript. Paraphrases,
unreviewed criteria, excerpt-free positive ratings, and duplicate assessments are
discarded, with a technical issue. Other valid evidence and a valid spoken reply
remain available. Requests and closing turns never receive technical ratings;
accidentally generated evidence is discarded without discarding the role answer.

If decision JSON is malformed, empty, refused, truncated, or rejected by controller
checks (such as a wrong question ID or ungrounded follow-up), the binding discards
that decision and repeats the reviewed technical prompt with no additional provider
request. The report retains the safe rejection reason. Repeated failures
use the existing follow-up/request and time limits, then progress through the plan.
The transcript remains in the report with a technical issue and no generated
assessment; that fallback turn does not establish skill coverage or insufficient
evidence. Valid assessments from other turns remain available. An answer with no
usable ratings, or only `not_assessed` ratings, remains **not assessed** rather than
being automatically labeled insufficient evidence. Report uncertainty and the UI
identify interrupted turns for human review; no past transcript is rescored.

Closing uses focused instructions with no assessment rubric history, and job facts
only. Whole, unambiguous no-more-questions replies in the five supported languages
finish locally without a provider request; replies containing a role question do
not match that shortcut. Other closing questions are answered from role facts, or
the recruiter is identified as the source for missing facts. An unusable closing
decision uses a localized uncertainty response instead of repeating the opening
question until the time/request limit ends the session.

Technical vocabulary from recruiter inputs is supplied as possible terminology
in the decision context. An ambiguous transcript should trigger one confirmation
with no assessment before clarification; original transcripts and evidence words
are never silently rewritten. The pinned `saaras:v3-realtime` endpoint is retained.
Sarvam documents [keyterm prompting](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/how-to/keyterms)
for v4 only; this change does not send unsupported keyterms to v3 or claim improved
acoustic recognition. Validate actual recognition in a browser conversation.

`app/interviews/controller.py` owns section/question indices, accepted finals,
follow-up/request counts, coverage, active time, event sequence, and response state.
The immutable reviewed plan resolves the current question ID. Finals are reserved
and checkpointed inside STT's existing accepted-final callback, before memory and
the user aggregator receive a final transcription. Connection plus provider turn
identity deduplicates finals. The aggregator remains the sole response trigger.
The governed context (including bounded recent history, pinned facts, summary and
recall) is passed into the decision request as quoted source data. It supplements
answer understanding but cannot override controller progression or the reviewed rubric.

Interview mode replaces the existing LLM slot with `InterviewResponseProcessor`.
One bounded Cerebras request returns a strict structured decision. The wire schema
uses the [documented Cerebras strict subset](https://inference-docs.cerebras.ai/capabilities/structured-outputs);
Pydantic also enforces lengths, enum values, and evidence shape. Controller checks
question identity, rubric membership, exact answer excerpts, grounded follow-ups,
one-question prompts, follow-up/request limits, section budgets, and closing reserve.
Only validated `<en>`, `<hi>`, `<kn>`, `<mr>`, or `<te>` spoken text enters the existing
language-prefix/TTS path. Internal assessments never enter candidate captions,
conversation memory, or speech frames. Recruiter source documents remain quoted
untrusted user data in the decision request, below code-owned system instructions.

The structured decision is buffered **in full** before speech; this increases first
speech latency compared with the general demo, whose streaming path is unchanged.
Provider request timeout, model, temperature, completion cap, and reasoning settings
come from the existing snapshotted runtime configuration. For longer rubrics, review
the completion cap in Settings; truncation uses the unassessed fallback rather than
executing partial JSON. SDK retries are disabled. An intentional Resume may retry one saved
pending decision without appending or replaying its accepted answer into STT.

Decision failures now identify timeout, provider HTTP status, truncated completion,
JSON/schema validation, controller rejection, or checkpoint I/O. The local
`Interview decision failed` log includes session/call IDs and a redacted provider
diagnostic where available; rejected JSON, assessment inputs and private reasoning
are not logged. Recoverable output problems produce an
`Interview decision recovered with reviewed prompt` warning with a safe diagnostic
and a technical issue in the report. For repeated `decision_truncated` warnings,
review LLM max tokens in Settings (up to 2048). Actual timeouts, provider HTTP/network
failures, checkpoint I/O failures, and unexpected internal errors still pause;
the candidate sees a concise safe reason. For an HTTP error,
use its status/code/parameter and the local redacted diagnostic to fix the provider
request. Current question ID and rubric criteria are constrained by the request
schema as well as controller validation. Output is kept concise to reduce truncation.

Repeat/clarification requests are retained as requests and do not become assessment
evidence. Technical answers can produce a grounded follow-up or move to reviewed
coverage. A model cannot finish early and skip core questions; section/overall time
limits can move to closing. Requests are bounded to prevent endless loops. The
manager-owned deadline closes silence at the active-time budget, waiting for an
already running reply to stop before delivering closing speech. Input remains
reserved during decision generation and until bot speech stops.

## Persistence and recovery

`data/interviews/definitions/<id>.json`, `sessions/<id>.json`, and `reports/<id>.json`
are separate typed, versioned, atomic records. Existing conversation envelopes and
history remain readable. Answers/decisions, question changes, consent, monitoring,
and human notes checkpoint before their result is acknowledged. Session state is
authoritative; reports are derived and can be reconstructed after a report-write
failure. Files are atomic individually, **not a database transaction across files**.
Keep the server running if a write error is reported, and retry after fixing storage.
The retained Retry saving flow retries both interview checkpoints and the original
conversation log without finalizing the conversation a second time.

The existing browser manager owns live Pipecat objects, peer signaling, cancellation,
and finalization. Live interview bindings never enter `ACTIVE_SESSIONS`.
Cleanup drains the runner before pausing the interview and finalizing the original
conversation once. Resume retains answered questions, evidence, IDs, and current
prompt; spoken context for the latest six accepted turns is restored into the new
media call. The existing per-call memory/metrics lifecycle still applies.
Paused wall time is excluded. On process restart, a formerly live session becomes
paused with an explicit interruption issue; time after its last checkpoint is
unknown and excluded. This is intentional recovery, **not seamless media recovery**.

## Local camera boundaries

To use phone detection, create a new interview and enable **local mobile-phone
detection** under Local camera settings before drafting/activation. It defaults off
for compatibility with existing definitions. The candidate sees whether it is enabled
and must explicitly consent to local face/phone analysis and metadata delivery.
Only the `cell phone` category is allowed; books, other objects, screen content and
phone use/intent are outside this feature. The report labels sightings **Possible
phone detections — human review**; model scores are not calibrated probabilities.

The same CPU worker analyzes faces at the configured camera cadence and phones at
2 fps by default. Phone settings (score threshold .7, sustained observation 2 seconds,
cooldown 15 seconds) are recruiter-editable in the immutable job snapshot. Low scores,
missing observations and long inference gaps reset a pending phone event. Phone
model/inference exceptions are isolated from face analysis where possible. A stalled
shared worker marks analysis unavailable; voice participation continues. No pixels
are uploaded, and camera observations never affect technical assessment.

Version-1 monitoring runs are keyed by the server-owned `call_sid`. Per-detector
cumulative counts, first/last analysis times, covered/unknown seconds, status and
bounded reason-coded gap histories are sent on the existing heartbeat route.
Retries replace a validated snapshot; they never add its counters again. Resume
creates another run; paused time is excluded. There are at most 100 runs and 64 gap
records per detector per run; truncation is explicitly marked. Disabled detectors
make no frame/coverage claim. Legacy records without summaries display **unknown**.

Coverage is a client-reported cadence estimate between successful analyses separated
by at most max(1 second, 3/configured fps). Loading, long gaps and unconfirmed tails
are unknown, not successful coverage. The server validates ownership, enabled flags,
monotonic counters/times, configured cadence and active-time bounds. This does not
independently verify a candidate-controlled browser or establish continuous footage.
Manual Pause attempts a bounded final flush before ending the media call; page exit
uses best-effort keepalive metadata. Delivery failures remain unknown. Consent
withdrawal closes the run and stops camera resources.

Face Detector in a dedicated worker uses CPU delegate and VIDEO mode with all
detections retained. It does not cap detection at one face. Its setup follows
[Google's web guide](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector/web_js)
and [official model documentation](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector).
Default analysis is 4 fps at 640×360. One transferred ImageBitmap is in flight;
busy frames are skipped. The worker closes each bitmap. End, pause, failure, and
page cleanup terminate workers, stop tracks, clear timers, and detach preview.
Capability/model/inference failures and stalled results produce explicit unavailable
status. This MVP omits orientation, recognition, recording, and avatars.
Optional phone detection uses the locally hosted EfficientDet Lite0 model in the
same worker. Enable it in recruiter camera settings before creating the candidate
link. Defaults require a model score of 0.7 sustained for 2 seconds; sightings are
neutral review events, not conclusions about use or misconduct. The preview reports
possible visible phones so a person can check lighting, framing and sustained visibility.
For troubleshooting, the worker retains phone candidates down to score 0.3 locally;
the configured recruiter threshold still gates every report event. The preview shows
the latest/best phone score, event threshold, sighting progress, timer resets,
event creation count and separately acknowledged saved/unconfirmed delivery counts.
These extra diagnostics are not sent in heartbeats or persisted. A queued event is
confirmed saved only after the server acknowledges it; failures remain visible
through subsequent frame updates.
No candidate above the diagnostic floor is different from an inference failure;
neither absence of candidates nor processed-frame counts establish recognition accuracy.
Phone rules tolerate one isolated missed sample within the existing cadence-derived
gap bound. Repeated misses, invalid observations or long gaps reset the sighting.
An event still needs the configured time span and at least
`ceil(phone_sustain_secs * phone_fps) + 1` actual above-threshold observations.
An event's duration is the observation span and may contain an isolated missed
sample; it does not establish uninterrupted phone visibility. Cooldowns remain in force.

The candidate page reports active only after the first valid analyzed frame, not
just successful model loading. Bitmap creation, first-frame availability, and
inference each have a watchdog; failure records unavailable status. Cleanup retains
unavailable/not-started states, and stale active heartbeats are reported unavailable.
The report explains that empty events and the latest stopped status cannot establish
continuous monitoring. Detector accuracy and actual devices require manual validation.

Recruiter-editable job settings control fps (3–5), confidence, displacement distance,
sustained duration, and per-type cooldown. Initial absence/position thresholds are
3 seconds, multiple-face threshold 2 seconds, cooldown 15 seconds. Brief changes,
low-confidence multiple detections, and long inference gaps do not count as sustained.
These are development defaults requiring human validation, especially in poor lighting.

Only event ID/sequence, session-relative timing, neutral type, counts/confidence and
heartbeat status go to FastAPI. No image, landmarks, bounding boxes, recording, or
identity is uploaded. Server checks scoped token, consent, type/shape/timing, receipt
timestamp ownership, sequence/retry deduplication, a 40 updates/minute limit, and a
2,000-event cap. Client metadata delivery is serialized and bounded to 20 queued
updates with at most three attempts. Heartbeats run every 10 seconds; a latest active
heartbeat older than 20 seconds is shown as unavailable. Not-started, unavailable,
stopped, and active-with-no-events are distinct. New run summaries preserve bounded
analysis/gap history; old records retain only their latest status and event timeline.
Camera events cannot enter technical generation or alter technical ratings.

Browser observations can be wrong or manipulated and are not independently verified
evidence of cheating, honesty, attention, emotion, personality, identity, or suitability.
No automatic rejection or hiring recommendation is generated. Recruiter reports show
reviewed rubrics, actual excerpts and answer IDs, missing evidence, uncertainty,
technical issues, a separate camera timeline, and human-owned notes/decision fields.

### Plain-English report summary

Below the detailed JSON report, the recruiter page displays a readable section-rating
table, criterion reasons, phone-sighting result/timestamps, face observations and
technical issues. It derives these from the existing report in the browser, with no
extra LLM call, route or persisted record. Existing saved interviews also receive it.

For each question/criterion, use the strongest usable saved assessment across answers
and follow-ups. Positive ratings require an exact accepted-answer excerpt. Requests,
closing turns, decision-fallback/transcription-ambiguity assessments and invalid quotes
cannot establish technical ratings; valid retained evidence after speech repair can.
No assessment is treated as a zero. If some criteria are missing, label the section
Partial assessment; if all are missing, Not assessed. With complete criterion coverage,
show Insufficient evidence when all criteria have that rating, Mixed evidence when it
coexists with positive evidence, Developing when any supported criterion is developing,
Meets reviewed criteria when all meet or exceed it, and Strong when all are strong.
Closing is explicitly Not scored. Criterion reasons and answer-turn links remain visible
for review; camera events never affect the ratings. These summaries describe recorded
rubric evidence, not a validated numeric ability score or automatic hiring decision.

Phone outcomes distinguish a saved possible sighting, no saved sighting despite
processed frames, unavailable monitoring, disabled/no-consent monitoring and older
records with unknown coverage. No saved phone event never establishes that a phone
was absent. Coverage includes the full active interview duration for comparison.

## Access and local-development limitations

Recruiter routes, existing demo/config/history, and API documentation require both
a loopback peer and local Host. Candidate routes use bearer tokens scoped to one
session and explicitly allowlist returned participation data. Camera/signaling/end
requests validate the same scope. Candidate tokens grant no recruiter/report access.
The camera worker has a same-origin CSP blocking third-party connections. The
candidate page additionally allows the existing voice SDK's Daily media helper
from Daily's CDN/fallback domains and local blob workers. It pre-creates the same
Daily call object and preloads its helper with `avoidEval: true` before the existing
device manager calls startCamera, following the [official CSP guide](https://docs.daily.co/guides/privacy-and-security/content-security-policy).
This retains SmallWebRTC's existing device manager, without a Daily room or video
call. The local camera stream is never passed to that manager or to video transport.
Responses are no-store with no-referrer; tokens never appear in query strings.

This is **not authenticated public recruiter hosting**. Keep binding to loopback.
An unguessable URL is not dashboard authentication. Do not expose it behind a reverse
proxy that turns untrusted users into loopback peers; public deployment needs explicit
authentication, TLS, proxy policy, and transport/network review. Remote microphone
and camera access also require a secure browser origin. No tunnel/TURN/auth platform
is introduced by this change. Bearer links must be kept private.

## Verification and manual acceptance

### Regression fixes after the October 29 interview reports

- Validated exact-excerpt evidence survives an invalid generated follow-up. Speech
  containing control data, multiple questions or an ungrounded follow-up is replaced
  with a localized clarification. Wrong question IDs and invalid evidence still cannot
  establish assessments. Bracket notation such as `{}` is permitted in technical speech.
- Clear procedural explanations are retained as answers when contradicted by a
  request classification. Explicit/mixed questions remain requests. Recognized fragments
  such as “However,” request completion locally, without negative ratings or an LLM call.
  These narrow guards do not reconstruct missing speech or guarantee intent classification.
  If a substantive continuation follows that completion prompt and the model omits
  its next spoken reply, ask for approach details rather than repeating that the
  answer is incomplete. Preserve independently validated evidence.
- Interview Sarvam finals wait for the existing snapshotted `turn_stop_timeout_ms`
  before checkpointing. Speech resuming during the window retains accepted chunks and
  commits their combined text once. Default hold is 500 ms, in addition to provider
  silence endpointing and the existing aggregator debounce. The general demo retains
  immediate final acceptance. Close/mute/provider failure discard unreleased chunks;
  a checkpoint already in progress is drained before teardown. DEBUG timing logs record
  speech start/end, final arrival and release without adding transcript logging.
- Drafts suggest recruiter-supplied skills using topic terms and shared rubric criteria.
  Review/activation require at least one supplied skill per technical question. Review
  these suggestions; they are not semantic proof of relevance. Complexity/correctness
  summaries require corresponding criteria. Existing activated definitions remain immutable.
- Known algorithm problems receive direct problem-solving prompts. Administrative
  closing topics are omitted from technical sections; one final candidate-question
  section is reserved. Independence/hint judgments are left for human review, rather
  than treating repetition or completion prompts as algorithmic assistance.
- Reports flag all technical-turn issues even when skills are unmapped, unasked
  questions and time-limit completion. They retain earlier interruptions after resume.
  Closing role facts exclude interview duration and assessment criteria.
- Camera shutdown before voice shutdown leaves the remaining active time unknown;
  report views do not modify saved observations. The report also displays time outside
  monitoring runs. Neither interval is successful analysis.

Create a **new draft** to test corrected planning. Copying an old reviewed plan
preserves its custom wording and mappings, so repair those in the plan editor before
review/activation. Old recordings/transcripts are never re-scored automatically.

For a human acceptance run, explain an algorithm with natural pauses, try “However,”
and “I don't know,” request a repeat, and ask a closing role question. Confirm the
complete accepted transcript before trusting ratings. With phone detection enabled
and consent, hold a real phone clearly visible for several seconds, then remove it;
also try brief appearances and non-phone objects. Confirm preview feedback and a
single sustained event, then unplug the camera while voice continues and inspect
unknown coverage. Offline tests verify event flow with synthetic detector results,
not real phone recognition or audible turn quality.

```powershell
.\.venv\Scripts\python -m pytest -q -m "not live"
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run prepare:vision
npm.cmd --prefix frontend run build
```

Automated tests mock providers/detector outputs; they validate protocol ordering,
state, authorization, persistence, time rules, deduplication, and cleanup. They do
not validate audible quality, detector accuracy, actual device behavior, or latency.
No billable provider request is made by these checks.

- Run a complete reviewed interview; inspect every question, answer excerpt, missing
  criterion, follow-up link, not-assessed skill, completion status, and human notes.
- Repeat in English, Hindi, Kannada, Marathi, and Telugu; check native script,
  pronunciation, selected language, one question at a time, and no spoken tags/JSON.
- Answer vaguely, say “I don't know,” request repetition/clarification, ask a closing
  question, and deliberately correct an STT mistake. Check attribution and progression.
- Confirm greeting waits for playback/provider readiness and both mute gates prevent
  barge-in. Check the general demo and old saved-conversation pages as well.
- Check console/network policy errors during the first Start and a Resume. Daily's
  existing audio helper may load from its CDN; vision model/WASM must load locally.
  Confirm no candidate images appear in requests, logs, provider payloads, or reports.
- With consent, leave frame for >3 seconds, introduce a second face for >2 seconds,
  then shift position for >3 seconds. Try brief motions and cooldowns. Inspect neutral
  events, confidence, and timestamps; never use these observations as hiring evidence.
- Test poor lighting, camera denial/unplugging, missing model/WASM, unsupported worker
  APIs, hidden tabs/stalled frames, consent withdrawal, and unavailable/no-events labels.
- Disconnect networking, pause/resume, refresh, restart the server, and retry a provider
  failure. Verify one runner, saved pending answers, explicit recovery prompt, paused
  time exclusion, no duplicate assessments, and an honest interruption issue.
- Let the duration expire while silent and during a reply. Test explicit permanent
  end and normal closing; confirm camera lights/worker/microphone stop and files save.
- On a laptop without a discrete GPU, monitor CPU and smooth camera preview while
  speaking; validate voice responsiveness and tune fps/confidence only from real trials.
