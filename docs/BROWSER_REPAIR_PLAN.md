# Browser pipeline repairs

Implemented on 16 October 2026 after the [debugging review](BROWSER_DEBUG_REVIEW.md).
The scope remains one local browser, one Python worker, and local JSON files.

| Slice | Change | Verification |
|---|---|---|
| Shutdown and saving | Keep cleanup alive after an HTTP timeout, await runner exit, finalize once, retain failed writes for retry | Slow runner, simultaneous End/disconnect, failed write, canceled retry, exactly-once finalization |
| Browser protocol | Pin client-js 1.9.0 and SmallWebRTC transport 1.10.2 to RTVI 1.3.0 | Build guard, installed SDK message parsing, native local WebRTC handshake |
| Cerebras | Qwen 3.8 27B, reasoning disabled, 1024 completion tokens, real request deadlines without SDK retries | Wire request checks, stalled normal request/draft, draft stream closure |
| Eager controls | Restore Settings fields and dependency validation; retain existing final-match/resume/mismatch behavior | Form round trip, invalid settings, controller/gate regression suite |
| Input boundary | Supply silence while the bot speaks and reject muted-turn transcripts before language/memory/history/drafts | Audio-frame rate/channel checks and rejected-turn checks |
| Browser feedback | Mute input during each reply, show unconfirmed saves and Retry saving, expose mic label and log allowlisted capture settings | Page callback, playback recovery, failed-save/retry and network-failure checks |

The call-log envelope and public routes are unchanged. Observers remain on PipelineTask;
Silero and UserBotLatencyObserver remain present. The pipeline boundary stays at 16000 Hz.

## Configuration applied locally

- Model: `qwen-3.8-27b`; reasoning: `none`; completion budget: `1024`.
- Eager EOT: enabled with threshold `0.5`; speculative LLM: enabled.
- Final EOT threshold: `0.65`; silence fallback: restored from `500` to `700` ms.
- Post-STT debounce: preserved at `500` ms; readiness wait: preserved at `30` seconds.
- Shutdown/HTTP wait: `30` seconds; retained cleanup continues after that wait.
- Voice, prompts, temperature, memory, provider credentials, and saved history are preserved.

The earlier local settings are backed up at `data/runtime_config.before-debug-fixes.json`.
This file is local and ignored by Git. These are trial settings, not recovered personal MongoDB
tuning or measured latency results. The user subsequently required no reasoning, so Qwen replaced
the initial GPT-OSS choice: Cerebras cannot turn off GPT-OSS reasoning. The configuration immediately
before that switch is backed up in `data/runtime_config.before-no-reasoning.json`.
New installations use Qwen with reasoning disabled and eager/speculative
flags off until enabled in Settings.

## What happens when End is slow

The HTTP request can return `saved=false` while cleanup is still running. The page offers Retry
saving. The retained task still owns cancellation, runner completion, finalization, and persistence.
A new conversation cannot replace a live runner. Once the runner exits, finalization runs once.
If the disk write fails, the finalized payload remains in memory and Retry saving retries only
the write. A hard process exit can still lose unsaved memory; keep Python open during a retry.

## Final verification

Completed: **256 non-live Python tests passed**, three live tests deselected;
**10 JavaScript tests passed**; frontend build and `git diff --check` passed.

Run the normal Python suite, the JavaScript tests, and the frontend build. The JavaScript suite
includes the installed SDK's real parsing of RTVI 1.3 messages with transport I/O stubbed; the
native WebRTC test separately uses real peers and silent audio with providers replaced.
Neither test establishes audible behavior. Live provider tests remain opt-in.

No connected browser was available for automated visual/device validation during this repair.
The remaining human check is required before calling the voice demo validated:

1. Restart Python and reload the page to load the rebuilt browser client and local configuration.
2. Hear the complete greeting; confirm the displayed microphone is the intended device.
3. Try English, Hindi, mixed speech, and a pause within a sentence. Compare headphones and speakers.
4. Speak during a reply: this version listens between replies and does not support barge-in.
5. Try Mute, then End during speech and silence. If offered, use Retry saving.
6. Open the saved history, check transcript and metrics, and start another call without restarting.
7. Compare eager/speculation on and off with the same prompts before changing endpointing or debounce.

Relevant provider documentation: [Cerebras reasoning and token budgets](https://inference-docs.cerebras.ai/capabilities/reasoning),
[Deepgram EOT parameters](https://developers.deepgram.com/docs/flux/configuration),
and [official client release history](https://github.com/pipecat-ai/pipecat-client-web/releases).
