# Sarvam speech migration

The existing browser agent now uses Sarvam realtime STT and streaming Bulbul v3 TTS.
Cerebras generation, context aggregation, memory governance, 16 kHz audio boundaries,
observer registration, persistence envelopes, and session cleanup remain in place.

## Changed files

| Files | Purpose |
|---|---|
| `app/services/sarvam_realtime_stt.py`, `pipecat_pipeline.py` | Realtime protocol mapping, accepted-final gating, provider readiness, and speech-service construction. |
| `app/services/language.py`, `language_prefix.py` | Five language tags, native-script policy, typed Sarvam language updates, tag removal, and localized empty-output fallback. |
| `app/services/browser_session.py` | Apply tagged greeting language before directly queued speech; retain readiness/cleanup ownership. |
| `app/services/memory.py` | Preserve Kannada/Telugu combining marks in recall tokens. |
| `app/services/turn_collector.py` | Select the existing generic Pipecat commit-source value while retaining historical Flux compatibility. |
| `app/config.py`, `.env.example`, `app/models/runtime_config_models.py` | Sarvam credentials, typed behavior settings, compatible voices, legacy migration, and unavailable-feature validation. |
| `app/routers/dashboard.py`, `app/templates/config.html` | Update affected Settings fields and availability labels. |
| `tests/test_sarvam_speech.py` and affected existing tests | Offline event/stream mapping, readiness/cancellation, once-only response and memory, mute rejection, routing, configuration, greeting, and route coverage. |
| `README.md`, configuration/architecture/latency/metrics/runbook docs | Current provider behavior, startup, compatibility notes, and human validation steps. |

No new project dependency, route, persistence envelope, observer registration, or
frontend JavaScript change was required. `.gitignore` excludes local verification downloads.

## Compatibility choices

- Pipecat stays pinned at 1.2.1. Its Sarvam websocket TTS supports stable `bulbul:v3`.
  The default multilingual voice is `shubh`; Settings offers compatible v3 voices.
- The pinned built-in Sarvam STT targets the legacy endpoint. The small
  `app/services/sarvam_realtime_stt.py` adapter uses the current realtime protocol,
  existing websocket dependency, `saaras:v3-realtime`, and `mode=transcribe`.
- Streaming TTS uses a 30-character buffer, the minimum accepted by the provider.
  The original 20-character default was rejected during configuration. Previously
  saved buffers below 30 migrate to 30 on load; new invalid edits are rejected.
- STT auto detection uses `auto`, while specific languages and TTS use `en-IN`,
  `hi-IN`, `kn-IN`, `mr-IN`, and `te-IN`. Language selection stays in the same LLM call.
- Readiness requires `session.begin`. A socket handshake alone is insufficient.
  Failure closes the existing session through the existing provider-error hook.
- Speech start latches acceptance. Partials are interim frames. Speech end does not
  commit: only a nonempty accepted final can reach collection, memory, or generation.
  Duplicate finals are suppressed before any of those side effects. The existing
  collector's rejection also prevents the final from entering the response path.
- Accepted finals emit finalized transcripts and a turn-release frame. The existing
  post-STT debounce remains because Pipecat system/data frames can be scheduled separately.
- Flux eager-EOT and speculation remain unavailable. Historical metrics retain their
  existing field names, and old conversation files remain readable.
- Recognized old speech configs migrate in memory. Saving Settings persists the
  migration; saved conversation snapshots are never rewritten. Old provider voice IDs
  become the default `shubh`, while generation and memory tuning are preserved.
- Indic combining marks are retained in lexical recall tokens for Kannada and Telugu,
  extending the existing Devanagari handling without changing memory design.

## Local startup (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
.\.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Use Node 22.14+ as required by the existing frontend. Your local `.env` needs
`SARVAM_API_KEY` and `CEREBRAS_API_KEY`. Keys stay server-side. Open
`http://localhost:8000/config`, review the migrated speech settings, save, then Start
at `http://localhost:8000`. Realtime access/quota must be enabled for your Sarvam account.

## Manual browser checklist

1. Confirm one complete greeting after readiness. Also try a configured Indic greeting
   prefixed with `<hi>`, `<kn>`, `<mr>`, or `<te>`; the prefix must never appear in speech/history.
2. With STT set to `auto`, ask a short question in each language:
   English, Hindi, Kannada, Marathi, Telugu. Check original-language user captions,
   native-script assistant captions, understandable audio, and exactly one response.
3. Explicitly request each other language mid-conversation; verify the next answer
   follows that request and no `<en>/<hi>/<kn>/<mr>/<te>` tags leak.
4. Speak through a pause, repeat a short phrase, and then try silence. Watch for duplicate,
   missing, or empty replies. Compare `auto` against each specific language setting.
5. Speak during greeting/bot playback, then use Mute/unmute. Rejected speech must not
   appear in history, update memory, or trigger a reply after unmuting.
6. Give a detail, converse past the recent-context window, then ask about it again.
   Check recall and inspect saved transcript/memory metrics.
7. End, restart, refresh, and test a second tab. Verify audio stops, history saves once,
   and the next conversation starts. Test an unavailable provider with disposable
   test configuration rather than exposing credentials.
8. Assess pronunciation, language detection, microphone/speaker behavior, and perceived
   latency yourself. Backend timing is not browser-speaker latency.

No automated check makes billable speech requests. Protocol/unit tests do not validate
audibility, pronunciation, transcription accuracy, echo behavior, or perceived latency.

## Verification performed

- `python -m pytest -q -m "not live"`: **294 passed, 3 live tests deselected**.
  This includes local WebRTC protocol wiring with substituted speech providers.
- `npm.cmd --prefix frontend test`: **11 passed**.
- `npm.cmd --prefix frontend run build`: passed.
- `python -m compileall -q app tests`: passed.

Backend checks used Python 3.11.5, the downloaded official Pipecat 1.2.1 wheel,
and a temporary local dependency overlay on the available Python environment.
The full isolated dependency installation was stopped after slow downloads;
use the normal installation commands above for your runtime environment.
Frontend checks used the available Node 22.12.0; startup instructions retain the
project's supported Node 22.14+ requirement. The only backend warning was Pipecat's
Python `audioop` deprecation. No microphone, speaker, pronunciation, transcription
quality, provider access/quota, or perceived-latency validation was performed.

Follow-up TTS buffer repair: the installed `.venv` passed **301 non-live tests**
with 3 live tests deselected. This includes outgoing TTS configuration, persisted
buffer migration, Settings validation, and credential-redacted provider errors.
The repair was verified offline; it does not establish live synthesis or audibility.

Follow-up disconnect diagnostics and meter repair: **72 focused backend tests** and
**12 frontend tests** passed; the browser bundle rebuilt successfully. The microphone
meter handles non-finite levels. STT retains redacted failure details, rejects late
events after failure, and emits only one fatal error. Its inherited language setting is
explicitly initialized while `language_code` continues to own provider routing.
The observed network/heartbeat timeout has not been reproduced or resolved by these
offline checks; no provider timeout was disabled or silently relaxed.

## Official references checked

- [Sarvam realtime STT guide](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming)
- [Realtime event/API reference](https://docs.sarvam.ai/api-reference/speech-to-text/transcribe/realtime/ws)
- [Sarvam Pipecat compatibility guide](https://docs.sarvam.ai/api/integration/pipecat-production-guide)
- [Streaming TTS choices](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/which-api-to-use)
- [Pinned Pipecat TTS source](https://github.com/pipecat-ai/pipecat/blob/v1.2.1/src/pipecat/services/sarvam/tts.py)

Newer Pipecat documentation describes realtime STT added in 1.8.0; it is not the API
installed by this project's 1.2.1 pin. No Pipecat upgrade is needed for this migration.
