# Browser pipeline debugging review

The repairs described here are now implemented; see [repair plan and validation](BROWSER_REPAIR_PLAN.md).
The findings below preserve the state observed before those changes.

Reviewed: 16 October 2026. This is a diagnosis and proposed repair plan. No application code,
provider settings, local configuration, or API keys were changed during this review.

## What the evidence says

I reviewed the supplied terminal log, the current code and installed SDKs, the browser migration
diff against `e401739`, and the local configuration and conversation directory. I also ran a
provider-free shutdown reproduction with in-memory settings and a mocked file writer.

The earlier browser plan deliberately disabled eager/speculative behavior. That simplification
removed access to existing tuning and should be reversed. The shutdown and protocol problems
are separate defects; restoring eager EOT alone will not repair them.

| Finding | Confidence | Effect |
|---|---|---|
| Eager EOT and speculative LLM are blocked by validation and absent from Settings | Confirmed | Existing latency features cannot be enabled |
| Browser RTVI 2.1.0 talks to server RTVI 1.3.0 | Confirmed | Unsupported event/protocol pairing |
| Slow shutdown can permanently skip saving and retain the session slot | Reproduced | Missing history, End 503, subsequent Start blocked |
| Current configuration uses Qwen and a 160-token completion limit | Confirmed | Requested GPT-OSS fallback needs coordinated settings |
| Flux silence fallback and post-STT debounce are both 500 ms | Confirmed settings | Short pauses can split speech; debounce can delay replies |
| Audio capture, echo, and recognition quality need a measured browser trace | Unresolved | Logs alone cannot identify the audio-quality cause |

## 1. Saving fails during slow shutdown

The supplied call is `browser_49b74c6c36d04cea9054aefe80421db8`. Its End request returns 503,
then the pipeline logs normal completion. There is no matching JSON file in this workspace;
the directory contains one older conversation. There is no file-write exception in the pasted log.

In `app/services/browser_session.py`, cleanup waits for the runner within the configured shutdown
budget, currently 10 seconds. On timeout it cancels the runner and waits only one more second.
If the runner still has cleanup to perform, the manager sets `_stuck` and returns **before**
`finalize_call_log()` and `persist_call_log()`.

When the runner later finishes, `_request_stop()` returns the already completed cleanup task.
It never resumes finalization. The session and stuck flag remain. This is a confirmed lifecycle
bug, not evidence that JSON replacement itself is broken.

The diagnostic reproduced this exact outcome using a simulated slow runner:

```text
End HTTP status: 503
Runner eventually completed: True
Save attempts: 0
Session slot still occupied: True
Server stuck flag: True
Cleanup completed without retry: True
```

The reproduction used a shortened in-memory deadline, not real providers. It establishes the
failure mechanism, but does not identify which component delayed this particular live call.
Pipecat's installed internal cancellation timeout is 20 seconds, already longer than the
application's 10-second budget. Provider websocket closure and processor cleanup also take time.

Proposed repair:

- Give one retained finalizer ownership of cancellation, runner completion, finalization, and saving.
- Keep awaiting the runner before finalizing; never save a supposedly final record while it can change.
- If an HTTP wait ends first, keep finalization alive and make repeated End requests join it.
  Use the existing End response shape to report an unfinished/failed save honestly.
- Release the session slot after actual cleanup. Distinguish a temporary slow stop from a runner
  that never finishes. A permanently live runner must continue to block replacement sessions.
- Align cancellation budgets with the installed library and log the duration of each cleanup stage.
  Increasing the timeout alone does not repair the missed-finalization path.
- On a disk-write failure, retain the finalized payload for retry before discarding session data.
  Currently the `finally` block deletes it even when saving fails.
- Add regression coverage for slow eventual completion, repeated End, concurrent disconnect,
  and a failed write followed by a successful retry. Assert exactly one finalized record.

The frontend also always displays “Conversation ended” in `finally`, including failed saves.
Keep an explicit save-failed or save-pending status and offer a retry through the existing endpoint.

## 2. The browser and server protocol versions do not match

Installed versions:

- Python `pipecat-ai==1.2.1`: RTVI protocol **1.3.0**.
- `@pipecat-ai/client-js==1.13.1`: RTVI protocol **2.1.0**.
- `@pipecat-ai/small-webrtc-transport==1.10.8`.

The warning is real. Working audio negotiation does not prove all transcript, readiness,
speaking, and error messages are compatible. The existing protocol test even advertises 2.1.0
to the older server and accepts the warning; it checks only basic readiness and silent media.

Proposed repair: keep the Python pipeline stable and select a client/transport pair verified
against its RTVI 1.3 protocol. Inspect candidate tagged releases and peer requirements before
pinning; do not guess a compatible version or just change the advertised version string.
Rebuild the bundle and test the actual JavaScript SDK against the server, including greeting,
transcripts, speaking callbacks, End, and error payloads. Fail verification on a protocol mismatch.

[Official client releases](https://github.com/pipecat-ai/pipecat-client-web/releases) identify
client-js 1.12.0 as the RTVI 2.0 release. That narrows the investigation but does not prove that
every earlier client works with this transport. Exact replacement pins remain to be validated.

## 3. Restore the latency controls already present

The eager event handlers, probe metrics, speculation controller, and response gate still exist.
The browser migration removed their form/parser controls and added an unconditional rejection
in `RuntimeConfig.validate_browser_config()`. Current JSON has both flags false and no eager threshold.

Restore Settings fields for:

- `stt.enable_eager_eot`
- `stt.eager_eot_threshold`
- `llm.enable_speculative_llm`

Replace the blanket rejection with the earlier dependency rules: speculation requires eager EOT;
enabled eager EOT requires a threshold no higher than the final EOT threshold. Keep strict validation
and snapshot the effective settings per call.

Eager telemetry alone does not reduce latency in this application. Stage 2 starts a draft early,
then releases its text only after final EOT when the transcript matches. A resumed turn or mismatch
must cancel/discard the draft and use the normal response path. Verify that the restored draft path
uses the same GPT-OSS request settings, handles empty/error streams, and never produces two replies.

The original values a person saved in MongoDB were not migrated into JSON. Git records the old
defaults, not those personal runtime edits. Do not claim to restore the exact old tuning without
that configuration. Start from recorded values if available; otherwise label proposed values as
new trial settings.

[Deepgram's configuration guide](https://developers.deepgram.com/docs/flux/configuration) confirms
that an eager threshold enables EagerEndOfTurn/TurnResumed, and that `eot_timeout_ms` forces a final
turn after silence regardless of confidence. It also explains the trade-off between earlier drafts
and more cancellations/API calls.

## 4. Return to Cerebras GPT-OSS 120B

Proposed next-call settings, not applied:

```json
{
  "model": "gpt-oss-120b",
  "reasoning_effort": "low",
  "max_tokens": 1024
}
```

Here `max_tokens` is the application's existing field; it must continue to map to API
`max_completion_tokens`. Use 1024 as an initial test budget, not a measured optimum. Keep spoken
answers short through the prompt. Check completion reason and actual visible output before tuning
the budget down. The current 160-token cap is unsafe to carry over blindly to a reasoning model.

[Cerebras reasoning documentation](https://inference-docs.cerebras.ai/capabilities/reasoning)
lists `gpt-oss-120b` with low/medium/high effort, no reasoning-off option, and reasoning tokens counted
within the completion budget. Reasoning and final content are separate in parsed streaming responses.
Only final-answer text should enter language-prefix parsing and TTS; never use raw reasoning for speech.

Apply the change through validated local Settings and update canonical defaults for new installations.
Existing JSON must be updated explicitly; changing defaults alone will not change this installation.
Verify the normal and speculative request paths. No authenticated model-availability or latency call
was made during this debugging round.

## 5. Speech boundaries and microphone quality need separate checks

The current file has `eot_threshold=0.65`, `eot_timeout_ms=500`, and
`turn_stop_timeout_ms=500`. These are different controls:

- Flux's 500 ms silence fallback can finish a sentence during a natural thinking pause.
- The separate Pipecat debounce can add waiting after final transcription. Its actual effect
  depends on frame order; measure it instead of assuming every turn costs exactly 500 ms.

The pasted log shows turns only 1.5–2 seconds apart, including incomplete phrases. That is consistent
with fragmentation, but there is no recording to prove whether the cause is pause handling,
recognition, echo, or the speaker's intended turn boundaries.

There is also a visibility problem: the frontend keeps the microphone enabled after the greeting,
while the server uses `AlwaysUserMuteStrategy()` to suppress user input during bot speech.
The aggregator is downstream of STT, language detection, memory, and the EOT persistence callback.
Consequently, hearing/recording a transcript does not guarantee the aggregator accepted that turn
for a reply. Audit these side channels against the same mute/accepted-turn state. This behavior
predates the browser migration, but browser speakers make echo particularly relevant.

Keep the existing no-barge-in baseline during this repair. Make the listening state honest and
ensure unwanted speech cannot update history, memory, or speculative drafts. Re-enabling eager EOT
does not itself add barge-in.

For audio debugging, collect the selected input device, actual track settings, mic-enabled state,
browser echo/noise/gain settings, and WebRTC loss/jitter statistics during a short human test.
The current page shows only a meter and relies on SDK device defaults. SmallWebRTC's installed
default media manager uses Daily's browser media library; this is not evidence of a Daily room or
hosted audio dependency. Inspect actual capture settings before replacing it or adding constraints.
Several mic operations are asynchronous but are not awaited by the page; check readiness/mute races.

Keep pipeline-facing audio fixed at **16000 Hz**. Device/WebRTC codec rates may differ normally.
Do not raise sample rates or replace Cartesia to guess at an unmeasured input problem.
Compare headphones and speakers, then English, Hindi, mixed speech, and intentional pauses.
If transcripts are correct but generated speech sounds poor, isolate the chosen Cartesia voice,
language switch, and output path next. [Cartesia's websocket API](https://docs.cartesia.ai/api-reference/tts/websocket)
documents separate language, output-format, context, completion, and error messages for that check.

## 6. Interpreting the remaining warnings

- The startup dropped-audio warning occurs before Flux is ready. Muted WebRTC can still supply
  silence. This does not prove that the person's first words were lost. Observe mic state and
  non-silent input at the readiness boundary; the warning text currently assumes an old greeting flow.
- Flux's “No audio received for 500 ms” warning means its watchdog supplied silence to finish a
  pending turn. It does not establish packet loss. Correlate it with mute/track state and frame timing.
- `ttfs_p99_latency not set` is a metadata fallback, not evidence that every reply gained one second.
  The configured external stop strategy does not use the metadata-based speech timeout strategy.
- The favicon 404 is cosmetic.
- The pasted log has no explicit Cerebras/Cartesia API failure. It also lacks saved per-turn metrics
  for this call, so provider timing and output truncation cannot be diagnosed from it.

## Repair order and acceptance checks

1. Repair shutdown/finalization first. Reproduce slow teardown and disk failure; prove eventual save
   and slot release after the runner exits, without duplicate finalization.
2. Align client/server protocol versions and verify real SDK events. Keep the current server API.
3. Apply GPT-OSS with low reasoning and an adequate completion budget; verify outgoing requests,
   final-text-only streaming, non-empty replies, completion reasons, and timeout behavior.
4. Restore eager/speculative controls and their validation. Exercise eager match, resume, mismatch,
   normal fallback, and cancellation while ending a call. Recover old tuning if available.
5. Trace accepted turns and input audio with a human. Tune Flux endpointing and post-STT debounce
   separately, comparing eager off/on only after the saving and event traces are trustworthy.

For the human acceptance session: hear the entire greeting, speak English/Hindi and mixed phrases,
pause mid-sentence, speak while the bot is speaking, use Mute, then End during speech and during
silence. Confirm the save result, open the matching history file/page, compare transcript and turn
metrics, and start another call without restarting Python. Also check refresh/disconnect saving.

The existing 246 Python and six JavaScript checks did not establish these live properties. The JS
checks use a mocked SDK; the native WebRTC test omits STT/LLM/TTS. Add targeted regressions for the
identified failures rather than treating those passing totals as proof that the demo is voice-ready.
This review made no live provider calls and did not validate audible behavior.
