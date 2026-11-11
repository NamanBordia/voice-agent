# ADR-007: Single-interview mode around the browser pipeline

Status: Accepted by the user on 2026-10-27, including the bundled route,
persistence, and MediaPipe dependency approval.

Add local recruiter setup, immutable reviewed plans, token-scoped candidate
participation, typed atomic interview files, and an explicit interview controller.
Retain the existing demo and fixed 16 kHz SmallWebRTC/Sarvam/Cerebras pipeline.
The accepted final callback checkpoints each candidate turn; the existing user
aggregator remains the single response trigger. Only validated spoken text passes
through the existing language-prefix and streaming TTS path. Structured decisions
are buffered, introducing a documented interview-mode latency tradeoff.

Use pinned MediaPipe Face Detector 0.10.32, official checksum-verified BlazeFace
version 1, self-hosted assets, CPU worker inference, explicit consent, and compact
neutral event/heartbeat records. Candidate images stay local. Camera assertions
are separate from answer assessment and cannot produce suitability judgments.

Keep one active media runner. Resume establishes new media and retains controller
checkpoints; process restart marks interruption rather than claiming seamless
recovery. Recruiter management stays loopback-only without authentication. No new
Python dependency, database, server worker, queue, or cloud vision API is introduced.

See [workflow and limitations](../INTERVIEWS.md) and the [approved implementation
scope](../INTERVIEW_IMPLEMENTATION.md).

## 2026-11-12: user-approved YOLO phone trial

The user approved the proposed YOLO11n browser trial with ONNX Runtime Web.
Replace only phone-model inference; keep MediaPipe faces, consent, the existing
worker, neutral phone events, current persistence, and separate technical ratings.
Pin the model/export hash and browser runtime, self-host their assets, and retain
WebGPU with single-thread WASM fallback. No new API, record shape, speech setting,
or person-detection event is included. See [trial details and validation limits](../YOLO_PHONE_TRIAL.md).

## 2026-11-12: user-requested return to EfficientDet-Lite0

The YOLO trial is superseded by the user's explicit request to restore
EfficientDet-Lite0. Keep MediaPipe 0.10.32 and the original checksum-pinned int8
version-1 phone model. Remove ONNX Runtime and YOLO assets from active builds;
restore the classic worker. Retain proportional frame capture, the new-interview
phone threshold of 0.40, existing confirmation/consent rules and truthful coverage
reporting. Face detection and interview/audio behavior are unchanged. A real-camera
comparison remains necessary; returning to the earlier model does not establish
reliable phone detection.
