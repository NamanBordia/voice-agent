# Approved camera coverage and mobile-phone detection

Prepared 2026-10-29. Status: approved by the user on 2026-10-29 and implemented.
The user selected mobile phones only as the initial object class.

## Approved scope

- Reuse installed `@mediapipe/tasks-vision@0.10.32` and the existing local vision
  worker. Add one self-hosted official EfficientDet-Lite0 model, pinned to version 1
  with a fixed SHA-256 checked by the setup script. Verify the selected artifact's
  availability/metadata before installing it. No new npm/Python package or route.
  Installed artifact: EfficientDet-Lite0 int8 version 1, SHA-256
  `0720bf247bd76e6594ea28fa9c6f7c5242be774818997dbbeffc4da460c723bb`.
  Its embedded `labels.txt` was inspected and includes `cell phone`.
- Run Object Detector with the `cell phone` category allowlist. Keep face and phone
  results/status distinct. Start phone inference at 2 fps, using the existing
  one-frame-in-flight rule and CPU worker; measure voice responsiveness manually.
- Extend recruiter-editable `Job.camera` settings with `phone_enabled` (default
  false), `phone_fps` (default 2), `phone_confidence` (default 0.7),
  `phone_sustain_secs` (default 2), and `phone_cooldown_secs` (default 15).
  Thresholds are starting values requiring human camera validation.
- Extend the existing compact camera-event type with `phone_detected`. Reuse its
  event ID, sequence, start/duration, confidence and receipt timestamp. The face
  count is absent for phone observations. No pixel data or screen contents.
- Extend existing monitoring JSON and heartbeat payloads with optional versioned
  run summaries, identified by `call_sid` and sequence. Each detector has a status,
  analyzed-frame count, first/last successful analysis times, reported covered
  seconds, unknown/unavailable seconds, and a bounded failure/gap history. Preserve
  the latest status fields for older clients; older records load with unknown
  metrics. Bound retained runs/history and validate counters/times server-side.
- Use cumulative counters with run ownership checks so retries do not double-count
  results and Resume starts another run. Send final metrics before ending the media
  call where possible; lost updates produce unknown coverage, never assumed success.
- Show per-detector coverage, frame totals, interruptions and a neutral phone-event
  timeline in the recruiter report. An empty event list is explicitly inconclusive.

## Candidate experience and boundaries

Explain face/phone monitoring and local processing before candidate consent.
Only enabled, consented detectors run. The candidate can withdraw consent. Release
all camera/model resources on pause, end and page exit. Phone initialization or
inference exceptions produce a phone-unavailable status; retain face monitoring
where possible and allow voice participation to continue.

A phone must be sufficiently visible to the camera. Detection may miss small,
occluded or poorly lit phones, or confuse another object for one. Require sustained
observations and a cooldown rather than flagging one frame. Report "Possible phone
detected" with time/duration and a model score (not a calibrated probability).
Do not infer phone use, screen content, cheating, intent or candidate suitability.
These unverified client observations never alter technical scores or hiring decisions.

No candidate video is recorded or uploaded. Only compact metadata is persisted.

## Review and validation

Verification on 2026-10-29: 443 backend tests and 43 frontend tests passed; both
model hashes verified and browser bundles rebuilt. An isolated headless Chrome
check loaded both actual CPU models/WASM and processed a blank synthetic frame
(zero faces/phones, no errors). This validates model wiring, not real-camera
accuracy, audible behavior, or voice responsiveness under real device load.

Automated tests: category filtering, sustained detections, confidence thresholds,
cooldowns, inference gaps, independent detector failure, first-frame readiness,
consent, cleanup, cumulative-update deduplication, run ownership, bounds, Resume,
old-record loading and report labels. Mocked results do not establish accuracy.

Manual browser checks: a real phone held at different distances/angles, screen
on/off, partial occlusion, low light, a remote control and other confusing objects,
face absence/multiple faces, camera denial/unplugging, model failure, Pause/Resume,
and voice responsiveness with both detectors enabled.

## Repository approval requirement

[AGENTS.md](../AGENTS.md) requires asking before:

> Adding dependencies not already present.
> Changing public route structure, the persistence shape, or observer registration.

The separate object-model asset and expanded camera/monitoring record shapes are
the scoped additions requested here. Existing voice transport, observer registration,
and 16000 Hz audio are outside this change.

## Primary references

- [MediaPipe Object Detector for Web](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector/web_js)
- [Object Detector models and configuration](https://developers.google.com/edge/mediapipe/solutions/vision/object_detector)
- [Official supported labels, including cell phone](https://storage.googleapis.com/mediapipe-tasks/object_detector/labelmap.txt)
