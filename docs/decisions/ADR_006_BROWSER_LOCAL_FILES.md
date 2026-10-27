# ADR 006: Local browser voice agent and JSON storage

Status: Accepted by the user, 16 October 2026.

The prototype now targets one person in a browser on the same machine as Python.
The user explicitly chose to remove Exotel and MongoDB and use local files.

- SmallWebRTC connects the browser directly to the FastAPI/Pipecat server.
- Deepgram Flux, Cerebras, and Cartesia remain server-side providers.
- Jinja/HTMX and a bundled plain JavaScript client serve the UI; React is unnecessary.
- `data/runtime_config.json` holds typed behavior settings and each conversation gets an immutable snapshot.
- `data/conversations/` holds atomically written, versioned `CallLog` envelopes.
- One process/worker owns one active conversation. Active-conversation crash recovery is outside scope.
- The pipeline-facing sample rate remains fixed at 16000 Hz. WebRTC handles native device/codec conversion.
- The human validates microphone/speaker behavior, multilingual quality, and perceived latency.

This supersedes Exotel-specific deployment/serializer assumptions and the MongoDB storage
part of ADR-002. ADR-001's fixed pipeline rate and the remaining separation/configuration/quality
rules remain in force. Old MongoDB data is left untouched and is not migrated automatically.
