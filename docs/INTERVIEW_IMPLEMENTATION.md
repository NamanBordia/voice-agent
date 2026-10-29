# Single-interview MVP implementation

The existing voice demo and its 16 kHz Sarvam/Cerebras/SmallWebRTC pipeline remain.
Interview mode adds a controller at the accepted-user-turn/context boundary. It
uses the aggregator's existing response trigger, validates one structured Cerebras
decision, then emits only language-tagged spoken text into the existing prefix/TTS
processors. JSON and assessment notes never reach speech or candidate captions.

## Scoped additions requiring repository approval

- Recruiter pages/API under `/interviews` and `/api/interviews`, restricted to an
  actual loopback client and local host. Existing management pages are also local-only.
- Candidate preparation at `/candidate/{session_id}` and scoped participation APIs
  at `/api/candidate/{session_id}/...`. Random participation tokens are validated
  against server-side hashes. Tokens grant no management/report access.
- Separate versioned atomic JSON records under `data/interviews`; existing
  conversation envelopes, history pages, and observer registration stay unchanged.
- Pin `@mediapipe/tasks-vision` to `0.10.32`; use the official BlazeFace short-range
  model and self-host its model and matching WASM assets. No new Python dependency.
  CPU detection runs in a dedicated worker at a configurable default of 4 fps.
  Face Detector returns all detected faces; no single-face cap is applied.

## Runnable stages

1. Typed job/plan models, deterministic source-grounded plan drafting, local setup,
   recruiter JSON plan editing/review, activation, and persistence tests.
2. Explicit controller, validated answer decisions and evidence, shared voice hooks,
   checkpointing, bounded failure pause, and exactly-once protocol tests.
3. Consent/device preparation, independent local camera stream, worker inference,
   sustained/cooldown event rules, authenticated compact events and heartbeats.
4. Rubric/evidence reports, human notes/decision fields, interruption/resume, restart
   recovery marking, and full non-live checks plus manual validation instructions.

## Important boundaries

Recruiter source documents are untrusted quoted data. Reviewed questions and their
rubrics define coverage. Controller state, not conversational memory, owns progress.
Decision generation buffers a bounded JSON response until schema and transition
validation finish; the speech path never acts on partial JSON. One authoritative
aggregator context trigger drives each response. Resume re-establishes media and
repeats the pending question with an explicit recovery introduction; it never
replays an accepted answer into assessment. Active time excludes pauses.

Camera observations are client assertions, can be manipulated, and are not evidence
of cheating, attention, honesty, emotion, or suitability. They are displayed separately
and cannot change technical assessments. Head orientation and recording are omitted.
No candidate pixels are sent to FastAPI, providers, telemetry, or logs. Detector/model
failure produces monitoring_unavailable and a visible voice-only fallback.

Official references checked:

- https://ai.google.dev/edge/mediapipe/solutions/vision/face_detector/web_js
- https://developers.google.com/edge/mediapipe/solutions/vision/face_detector
- https://docs.daily.co/guides/privacy-and-security/content-security-policy

The existing SmallWebRTC browser SDK internally uses DailyMediaManager for audio
devices. Candidate pages allow its documented CDN helper with `avoidEval: true`;
the separate camera worker is restricted to same-origin assets and connections.
The local camera stream never enters the voice SDK or WebRTC video transport.

The web guide documents synchronous inference and recommends workers to keep the
interface responsive. Assets must use the same pinned package version; inference
uses `delegate: CPU`, VIDEO mode, confidence/suppression thresholds, and local URLs.
