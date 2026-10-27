# Documentation

The application is a single-user local browser voice agent with SmallWebRTC and JSON-file storage.

| Document | Topic |
|---|---|
| [ARCHITECTURE](ARCHITECTURE.md) | Components, pipeline, readiness, and cleanup ownership. |
| [Sarvam migration](SARVAM_MIGRATION.md) | Provider compatibility, startup, and five-language manual checks. |
| [CONFIGURATION](CONFIGURATION.md) | Local settings, defaults, validation, and snapshots. |
| [LATENCY](LATENCY.md) | Streaming, endpointing, and honest browser measurement. |
| [METRICS](METRICS.md) | Observer records, timing anchors, and outcome taxonomy. |
| [MEMORY](MEMORY.md) | Recent window, facts, summary, and lexical recall. |
| [RUNBOOK](RUNBOOK.md) | Setup, checks, and human browser validation. |
| [Implementation plan](BROWSER_IMPLEMENTATION_PLAN.md) | Reviewed conversion plan and acceptance boundaries. |
| [Repair plan](BROWSER_REPAIR_PLAN.md) | Implemented pipeline repairs and remaining human validation. |
| [Debugging review](BROWSER_DEBUG_REVIEW.md) | Evidence behind the repairs. |
| [English conversation review](ENGLISH_CONVERSATION_REVIEW.md) | Chat evidence, current research, and focused quality improvements. |
| [ADR-006](decisions/ADR_006_BROWSER_LOCAL_FILES.md) | Accepted replacement of Exotel/Mongo with browser/local JSON. |
| [Decisions](decisions/) | Historical decisions; ADR-006 supersedes telephone/storage assumptions. |
| [SPECULATION](SPECULATION.md) | Historical Flux eager telemetry and speculative drafts; unavailable with Sarvam. |
| [TELEPHONY](TELEPHONY.md) | Retirement notice for the old telephone path. |

Start with the root [README](../README.md) to run locally.
