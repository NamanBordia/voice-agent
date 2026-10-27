# Browser Voice Agent

A single-user English, Hindi, Kannada, Marathi, and Telugu voice agent in your browser. FastAPI runs Pipecat locally;
SmallWebRTC carries microphone audio, speech playback, and UI events.
Sarvam realtime STT, Cerebras Qwen 3.8 27B with reasoning disabled, and Sarvam Bulbul v3 remain server-side providers.

No Exotel account, phone call, public tunnel, React server, or MongoDB is needed.

## Run locally

Use Python 3.11+ and Node 22.14+ for the initial browser build. Node is not needed to run built assets. In PowerShell, use `npm.cmd` if execution policy blocks `npm.ps1`.

1. Create/activate a virtual environment and install the project:
   `python -m pip install -e .`
2. Copy `.env.example` to `.env` if you do not already have one, and set
   `SARVAM_API_KEY` and `CEREBRAS_API_KEY`.
   Refresh existing values locally; never put them in the browser or commit them.
3. Build the browser assets:
   `npm --prefix frontend ci`
   then `npm --prefix frontend run build`.
4. Run:
   `python -m uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1`
5. Open [Settings](http://localhost:8000/config), review the Sarvam Bulbul v3 voice (default `shubh`), and save.
6. Open [Talk](http://localhost:8000), click Start, allow the microphone, and enable audio if prompted.

The agent listens after the greeting and between replies. Headphones help avoid echo.
End waits for cleanup and reports whether the conversation was saved. If saving takes longer
or a write fails, use Retry saving; cleanup continues while the server stays open.

## Local files

- `data/runtime_config.json`: editable behavior settings, seeded on first startup.
- `data/conversations/browser_<uuid>.json`: finalized transcript, metrics, outcome, and config snapshot.
- `logs/`: structured server logs.

Data and generated assets are Git-ignored. Old MongoDB data is not read or modified.
Each conversation keeps its own config snapshot. Saving Settings changes the next conversation.

## Verification

Run `python -m pytest -m "not live"` and `npm --prefix frontend test`; rebuild the frontend after JavaScript changes.
The suite includes a real local WebRTC/RTVI handshake using silence with providers replaced.
These checks do not establish audible voice quality or perceived latency. A person must validate those in the browser.

The retained live STT tests target Deepgram, not Sarvam.
Live provider checks are opt-in with `python -m pytest -m live`, after keys and the voice are configured.
They use API credit and are not a substitute for the human microphone/speaker check.

See [the documentation index](docs/README.md), [architecture](docs/ARCHITECTURE.md),
[runbook](docs/RUNBOOK.md), and [the implementation plan](docs/BROWSER_IMPLEMENTATION_PLAN.md).

See [Sarvam migration and five-language manual checks](docs/SARVAM_MIGRATION.md).
