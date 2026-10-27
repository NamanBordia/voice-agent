# Configuration

Only provider credentials and server settings live in `.env`. Behavior is stored in
`data/runtime_config.json`, edited at `/config`, and snapshotted before each conversation.

## Defaults

| Group | Default |
|---|---|
| STT | `saaras:v3-realtime`, automatic language detection, original-language transcription, fast stream, VAD threshold 0.3, silence 700 ms, minimum speech 250 ms. |
| Turn debounce | Pipecat post-STT delay 500 ms; separate from Sarvam endpointing. |
| LLM | `qwen-3.8-27b`, reasoning `none`, temperature 0.7, completion limit 1024, total request timeout 10 seconds. |
| TTS | Stable streaming `bulbul:v3`, `shubh`, pace 1.0, temperature 0.6, buffer 30 characters (provider minimum), fixed 16 kHz PCM. |
| Greeting | Directly spoken `Hello, how can I help you today?`; no extra LLM generation. |
| Readiness / shutdown wait | 30 seconds / 30 seconds. Cleanup continues after an HTTP wait expires. |
| Memory | Existing recent-window, facts, local summary, and lexical recall settings. |

The models in `app/models/runtime_config_models.py` define all ranges and supported values.
Misspelled fields and invalid model/reasoning combinations are rejected.
The no-reasoning profile uses Qwen with `reasoning_effort=none` in both normal and speculative requests.
GPT-OSS remains selectable but cannot disable reasoning; low reasoning and hidden reasoning still generate thinking tokens.
Sarvam credentials use server-only `SARVAM_API_KEY`; Cerebras still uses `CEREBRAS_API_KEY`.
Deepgram and Cartesia credentials are no longer required. The local `.env` is never migrated.
Languages are English (`en-IN`), Hindi (`hi-IN`), Kannada (`kn-IN`), Marathi (`mr-IN`), and Telugu (`te-IN`).
`auto` enables adaptive STT detection. Explicit response-language requests remain in the existing LLM path.
The default greeting uses English. Prefix a configured Indic greeting with `<hi>`, `<kn>`,
`<mr>`, or `<te>` to set its synthesis language; the tag is removed from speech/history.
Native scripts are requested for Indic replies, with natural English technical terms allowed.

Flux eager-EOT and speculative generation cannot be enabled on this path. Recognized old Flux
configs migrate in memory to Sarvam, preserving debounce and silence (capped at 5000 ms), and
turning speculation off. Recognized Cartesia configs use the new Bulbul defaults because their
voice IDs are incompatible. Cerebras, memory, prompt, and operations values are preserved.
Save Settings to persist migrated values. Historical conversation snapshots remain untouched.
Saved TTS buffers from 1–29 characters migrate to 30 on load; new Settings edits below
30 are rejected. This repairs the original 20-character default rejected by Sarvam.
Unknown/invalid current settings still fail validation; there is no blanket reset.

## Persistence rules

Startup creates missing storage and seeds defaults only when the config is absent.
Malformed existing config or unwritable storage fails startup clearly, without replacing data.
Saving Settings validates first, atomically replaces the file, then updates the cache.
A canceled HTTP request does not interrupt that write/cache transaction.

Active conversations keep their existing snapshot. UI saves affect the next conversation.
Manual edits to the JSON file are read on restart. Model, voice, prompt, greeting,
and timeout values are not read from old behavior environment variables.

Settings currently uses ordinary form submission with an HTMX-enhanced replacement of the
settings panel; validation/write errors are displayed. History remains at `/calls`.
The log-level setting is applied on application restart.

See [ADR-006](decisions/ADR_006_BROWSER_LOCAL_FILES.md) for the replacement of MongoDB storage.
