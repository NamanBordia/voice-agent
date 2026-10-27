# AGENTS.md - Voice Agent Global Rules

Active documentation lives under [docs/](docs/) — see [docs/README.md](docs/README.md) for the index.
Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/LATENCY.md](docs/LATENCY.md), and
[docs/METRICS.md](docs/METRICS.md). Accepted decisions are in [docs/decisions/](docs/decisions/).

## Mission

Help build a local-development, open-ended multilingual browser voice agent using Pipecat,
SmallWebRTC, Deepgram Flux, Cerebras, Cartesia, FastAPI, and local JSON persistence.

The human handles real microphone/speaker behavior, voice quality, and latency validation. The AI owns
code quality, architecture adherence, type-safe implementation, structured logging, and normal
non-voice verification. Never claim audible behavior works from unit/protocol tests.

## Operating Model

Implement in small, reviewable, test-backed slices. Don't pre-build speculative future work. If a blocker
materially affects the design, document it and stop for review. A human browser conversation is
required to validate voice behavior.

## Boundaries

### Always

- Python 3.11+ conventions and modern typing; type hints on public functions/classes/data.
- Pydantic for configuration and schema models.
- Keep browser transport/session ownership isolated from logging, schema, and config code.
- Browser transport's pipeline-facing audio and pipeline sample rate stay fixed at 16000 Hz (see `docs/decisions/ADR_001` and `ADR_006`).
  Do not introduce dynamic sample-rate configuration unless the human changes the requirement.
- Build audio-only `SmallWebRTCTransport` with input/output audio enabled at 16000 Hz.
- Configure Pipecat services through typed `.Settings()` objects.
- Register observers on `PipelineTask(..., observers=[...])`, not inside `PipelineParams`.
- Include `SileroVADAnalyzer()` in user-aggregator params even without barge-in, because latency
  observers depend on timing frames.
- Use `UserBotLatencyObserver` for latency capture.
- On browser ending/disconnect, await `task.cancel()`, let the runner unwind, then `finalize_call_log()` once.
- Use `stream_sid` and `call_sid` consistently (never `stream_id` or alternate names).
- `app/runtime.py` is the single source of truth for the runtime session registry
  (`ACTIVE_SESSIONS`) and session-state helpers. Store only JSON-safe data there — never live
  Pipecat/provider objects.
- Tuning/behavior config is runtime-configurable (local JSON, frontend-editable) and
  snapshotted per call; only secrets live in `.env` (see `docs/decisions/ADR_002`).

### Ask First

- Adding dependencies not already present.
- Changing public route structure, the persistence shape, or observer registration.
- Changing the 16000 Hz transport/pipeline assumption.
- Introducing background workers, queues, or infra not already planned.
- Cross-cutting refactors beyond the current phase scope.

### Never

- Commit secrets, tokens, or real `.env` values. Edit only `.env.example`, never `.env`.
- Invent fake "successful" voice tests or claim audible behavior was validated automatically.
- Register observers inside `PipelineParams`.
- Place tuning/behavior config in `.env` (secrets only).

## Testing Policy

The human owns: real browser microphone/speaker validation, voice quality, and latency realism.

The AI owns: project structure, config/schema correctness, import/module wiring sanity, route/flow
verification, serializer construction, logging payload shape, and persistence-model shape.

## Quality Standards

- Favor explicit naming; keep side effects easy to trace.
- Prefer small focused modules over broad files.
- Avoid hidden global behavior except deliberately shared runtime registries.
- Brief docstrings on public APIs; comments only where they reduce ambiguity.
