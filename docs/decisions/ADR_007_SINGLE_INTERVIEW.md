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
