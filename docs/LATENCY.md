# Latency

The browser uses the existing streamed Sarvam realtime → Cerebras → Sarvam Bulbul pipeline.
No latency number from the old telephone prototype is a browser performance guarantee.

## What stays fast

- Cerebras final-answer tokens stream into Sarvam Bulbul sentence synthesis; the full response is not buffered.
- Qwen runs with reasoning explicitly disabled in normal and speculative requests.
  The prompt keeps spoken answers short; 1024 tokens is an output ceiling, not a target to generate.
- VAD is warmed locally at startup. Sarvam realtime connection overlaps media setup.
- The greeting waits for browser playback and Sarvam realtime readiness instead of using a carrier settle delay.
- Bounded recent context and the existing memory budget keep prompt work controlled.
- Flux eager/speculative behavior is unavailable on the Sarvam path. Partials never start generation.
- Normal and draft requests have a total deadline and SDK retries are disabled.

## What to measure

The existing observer distinguishes acoustic/VAD-stop anchors, turn release/commit, LLM TTFB,
TTS first audio, and merged turns. See [METRICS](METRICS.md) for the stored fields.
These are backend events. Network jitter, codec buffering, and browser playback add time.

Sarvam `silence_duration_ms` controls server endpointing. Pipecat's separate
`turn_stop_timeout_ms` remains the post-STT debounce. The adapter releases a turn only
when its final transcript is accepted. Tune one variable at a time after a human baseline.
In interview mode, the same snapshotted turn-stop setting also supplies a bounded
final-transcript hold before checkpointing. Speech resuming during that window combines
accepted chunks before one response is reserved. This adds latency (500 ms at the
default) to protect natural pauses; browser conversations must validate the tradeoff.
DEBUG STT logs expose speech start/end, final arrival and final release timing.

The first greeting can wait for provider connection readiness. This trades some first-connect
speed for not losing the first utterance. It is separate from steady-state turn responsiveness.

## Validation ownership

The AI verifies configuration, provider request shape, metrics anchors, and protocol/lifecycle code.
A person validates the actual pause heard between speech and reply. Unit tests and silent media
tests do not prove voice quality, realistic latency, or echo behavior.
