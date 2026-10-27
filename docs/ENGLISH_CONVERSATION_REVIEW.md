# English conversation quality review

Reviewed on 16 October 2026. This is a research and diagnosis report; application code and runtime settings have not been changed for this review.

## Evidence from your conversation

I read the pasted conversation and checked the saved JSON and matching server logs for `browser_7685b148d21d4935b67fa0daaebcf36b`.

Your random questions, jokes, corrections, and slang are useful tests for an open-ended agent. They should not require a scripted conversation.

| What happened | What it tells us |
|---|---|
| "Card rates" and "card rails" were followed by your clarification, "I'm talking about guardrails." | Strong evidence of a recognition problem for that word. |
| "Scarecrow" appears as "clearcrow" in your follow-up to the scarecrow joke. | A likely word substitution, inferred from context. We do not have the audio to verify exactly what was spoken. |
| "You know, tell me a joke" was recorded, but the first answer was "Sure, what would you like to know?" | The request reached STT correctly. We also need to inspect context handling and response selection. |
| The assistant kept asking generic clarification questions and gave a blanket refusal about its guardrails. | Conversation behavior needs improvement independently of recognition. A useful answer could explain its general capabilities and limits. |
| There were five `language_prefix_parse` errors across 16 response records. One answer contains `</en>`. | The model/output protocol is unreliable in this call. This is separate from Deepgram transcription. |
| It could not answer today's date. | No trusted current date/time is supplied to the model. Recognition of that question was fine. |

Do not label every unusual phrase as an STT failure. Some were deliberate wordplay or banter. Without the input audio and an intended transcript, we cannot calculate a defensible word error rate.

This conversation was saved successfully. Its outcome is `user_disconnected`, with `completed=true` and no call-level error. That does not erase the per-turn errors above.

## What the research supports

### 1. Use the English Flux model for the English demo

The call used `flux-general-multi` with English and Hindi hints. Deepgram explicitly recommends `flux-general-en` for English-only workloads. It supports the same turn detection and eager events, so this is a small configuration change, not a pipeline replacement. Do not send `language_hint` to the English model. Our STT builder already omits it for that model. This is the correct starting profile, not a guarantee that every accent or word will improve. [Deepgram language guidance](https://developers.deepgram.com/docs/flux/language-prompting)

Keep the multilingual profile available for later. Use the English model only while this demo is English-only.

### 2. Compare browser noise suppression on and off

The actual call logged a Realtek microphone, one capture channel, and echo cancellation, noise suppression, and automatic gain control all enabled. Capture was 48 kHz; the pipeline boundary remains 16 kHz. Those different rates are expected in this architecture and are not evidence of a configuration mismatch.

Deepgram recommends testing preprocessing on representative audio. Noise suppression can remove useful speech detail, especially for accents, quiet speech, and short words. For a quiet-room comparison, keep browser echo cancellation enabled, compare noise suppression on/off, and leave gain control unchanged initially. Compare gain control separately only if level changes or clipping remain a concern. Use the same microphone, placement, and phrases. [Deepgram audio guidance](https://developers.deepgram.com/guides/deep-dives/audio-preprocessing-barge-in)

Our browser currently requests all three options as `true`. A future implementation should expose the capture choices through local runtime settings, rather than hard-coding another universal preset. No extra noise-removal library is needed for this comparison.

### 3. Add a short, relevant vocabulary list

Flux supports keyterm prompting. The installed Pipecat 1.2.1 service already supports `DeepgramFluxSTTService.Settings(keyterm=[...])`, but our application does not expose or populate it. Add a frontend-editable list of actual demo terminology, such as company/product names and technical terms. "Guardrails" is a reasonable candidate for this demo. Do not build a replacement dictionary that blindly changes "card rates" into "guardrails", and do not boost every word from a single joke. [Deepgram keyterm guidance](https://developers.deepgram.com/docs/keyterm), [Pipecat service reference](https://docs.pipecat.ai/api-reference/server/services/stt/deepgram)

Keyterms belong in typed local configuration and the per-call snapshot. Send separate terms using Pipecat's list support; do not combine them into one comma-separated query value or attach legacy keyword weights.

### 4. Tune turn timing separately from word recognition

Current settings are final EOT confidence `0.65`, silence fallback `700 ms`, eager confidence `0.5`, and Pipecat post-STT debounce `500 ms`.

Deepgram's EOT confidence describes whether the speaker finished, not whether the recognized words are correct. The silence timeout forces a turn to finish even below the confidence threshold. Raising these settings cannot be treated as a general accuracy upgrade. [Deepgram turn configuration](https://developers.deepgram.com/docs/flux/configuration)

First keep the EOT settings fixed while comparing the English model and microphone processing. If sentences still split at natural pauses, compare `700` versus `1000 ms` silence fallback. If model-triggered endings are premature, separately compare `0.65` versus `0.70`. Keep eager mode enabled. These are trial values and can increase waiting; they are not promised improvements.

## Application issues that also need attention

- **Language control tags:** A provider-free check of the current sanitizer confirms that closing `</en>` tags survive, including when split across text chunks. Fix both complete-text and streaming cleanup. For an explicit English response mode, let Python select English TTS and avoid requiring the LLM to generate language tags. Keep the existing multilingual path available. Stronger prompt wording alone does not provide reliable enforcement. [Deepgram voice prompting guidance](https://developers.deepgram.com/docs/prompting-voice-agents)
- **Merged requests and speculative context:** The first reply record merges "tell me a joke" with "tell me", while its eager/final preview matches only "tell me". The controller checks that latest transcript, not the complete pending request plus its context. Investigate whether a draft was built before the earlier fragment entered LLM history. This is a concrete risk, not a proven root cause without the exact request payload. Reuse must preserve all user fragments and remain valid for the context at release; otherwise use the normal response path. Keep this check cheap and deterministic.
- **Conversational behavior:** Use a short English voice prompt that answers clear requests directly, accepts corrections, handles playful banter, and asks a focused clarification only when the meaning is uncertain. Avoid automatic apologies and a generic follow-up question after every answer. Add a few short examples for corrections and clarification; do not add a second LLM to rewrite STT text. [Voice prompt design](https://developers.deepgram.com/docs/prompting-voice-agents)
- **Date/time context:** Supply the current local date and timezone from Python to both normal and speculative request builders. Refresh it when building a request so midnight changes are handled. This needs no web search service. Current officeholders and other changing external facts still require verified information; giving the model a date does not verify those answers.
- **Extra waiting after STT:** Latest commit-to-reply-release timing has a median of `497.9 ms` and ranges from `477.2` to `511.6 ms`. This closely matches the configured debounce. After verifying complete-turn context and event ordering, compare a `200 ms` debounce with the existing `500 ms`. Do not remove the wait blindly or promise that the full reduction becomes audible savings.

The call's median total server timing was `1757 ms`; median turn-release responsiveness was `722 ms`. Neither includes actual browser-speaker playback. All 16 responses used speculative drafts, so there is no normal-path baseline. The dashboard's estimated saving is not a measured saving. Streaming replay TTFB values near zero also do not mean Cerebras generated the reply instantly.

## Small implementation sequence

1. Establish an English-only baseline with `flux-general-en`, omitted language hints, and all other settings unchanged. Keep Qwen reasoning disabled and the transport/pipeline at 16 kHz.
2. Fix tag cleanup and verify merged-turn draft/context correctness. Introduce an explicit English response mode without removing multilingual support.
3. Add runtime microphone-processing controls and keyterms, with no dependencies or new routes. Compare processing settings first, then add vocabulary.
4. Improve the short voice prompt and supply trusted date/time in the shared normal/speculative context construction.
5. Tune post-STT debounce and Flux turn-ending settings separately, only after correctness checks pass.

Use the same short English test set throughout: a joke request, the scarecrow follow-up, "What are your guardrails?", a correction, a sentence with a mid-thought pause, a request spoken in two fragments, yes/no answers, today's date, and a game-engine question. Include ordinary sentences outside the vocabulary list to catch over-biasing.

Automated checks should cover settings round-trips and provider query construction, split closing tags, merged user requests, stale draft rejection, normal/speculative prompt parity, date/time refresh, and unchanged saving/muting behavior. A person must compare final transcripts against what they actually said and judge audible response time. If Flux remains worse on representative English audio after these comparisons, benchmark Nova-3 on matching samples before changing the live pipeline. Nova-3 does not supply Flux's EagerEndOfTurn/TurnResumed interface, so it is not a drop-in model-name change. [Flux/Nova comparison](https://developers.deepgram.com/docs/flux/flux-nova-3-comparison)
