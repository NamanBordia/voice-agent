"""Runtime registries and helpers for call state."""

from collections.abc import MutableMapping
import json
import time
from typing import Any, Literal, NotRequired, TypedDict, cast


from app.config import Settings
from app.services.runtime_config import build_effective_snapshot, get_cached_runtime_config


class ConfigSnapshot(TypedDict):
    """Immutable per-call config snapshot built from the global runtime config.

    Carries the structured ``runtime_config`` plus flattened legacy keys the
    pipeline/greeting code reads directly.
    """

    runtime_config: dict[str, Any]
    SYSTEM_PROMPT: str
    GREETING_TEXT: str
    LLM_MODEL: str
    LLM_TEMPERATURE: float
    TTS_VOICE_ID: str
    TTS_MODEL: str
    SAMPLE_RATE: int
    snapshot_version: NotRequired[str]
    interview_session_id: NotRequired[str]


class RuntimeSession(TypedDict):
    """Serializable runtime session state shared across phases."""

    call_sid: str
    stream_sid: str
    metrics: dict[str, Any]
    transcript: list[dict[str, Any]]
    first_latency: float | None
    config_snapshot: ConfigSnapshot
    language_state: NotRequired[dict[str, Any]]
    memory: NotRequired[dict[str, Any]]


ACTIVE_SESSIONS: MutableMapping[str, RuntimeSession] = {}


def build_config_snapshot(
    settings: Settings,
    profile_id: str | None = None,
) -> ConfigSnapshot:
    """Create an isolated snapshot of the loaded configuration."""
    config = get_cached_runtime_config()
    if config is None:
        raise RuntimeError("Runtime configuration has not been initialized")
    return cast(ConfigSnapshot, build_effective_snapshot(config))


def create_session(
    call_sid: str,
    stream_sid: str,
    settings: Settings,
    *,
    config_snapshot: ConfigSnapshot | None = None,
) -> RuntimeSession:
    """Create and register a new active runtime session."""

    if call_sid in ACTIVE_SESSIONS:
        raise ValueError(f"Session already exists for call_sid={call_sid}")

    session: RuntimeSession = {
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "metrics": {},
        "transcript": [],
        "first_latency": None,
        "config_snapshot": config_snapshot or build_config_snapshot(settings),
    }
    ACTIVE_SESSIONS[call_sid] = session
    return session


def get_session(call_sid: str) -> RuntimeSession | None:
    """Return an active runtime session by call SID, if present."""

    return ACTIVE_SESSIONS.get(call_sid)


def delete_session(call_sid: str) -> RuntimeSession | None:
    """Remove and return an active runtime session by call SID, if present."""

    return ACTIVE_SESSIONS.pop(call_sid, None)


# --- Phase 3: Session update helpers for metrics capture ---


def update_session_first_latency(
    call_sid: str,
    latency_secs: float,
    *,
    observed_at: float | None = None,
) -> None:
    """Set the first-bot-speech latency for a session (once only).

    Called from the on_first_bot_speech_latency observer callback.
    Writes to both session["first_latency"] and
    session["metrics"]["first_bot_speech_latency_secs"].

    Args:
        call_sid: The call identifier to update.
        latency_secs: Time from client connection to first bot speech.
    """

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    if session["first_latency"] is None:
        session["first_latency"] = latency_secs
        session["metrics"]["first_bot_speech_latency_secs"] = latency_secs
        session["metrics"]["first_bot_audio_started_at"] = observed_at or time.time()


def update_session_metric(call_sid: str, key: str, value: Any) -> None:
    """Set one JSON-safe metric value for an active session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    session["metrics"][key] = json.loads(json.dumps(value))


def merge_session_metrics(call_sid: str, metrics: dict[str, Any]) -> None:
    """Merge JSON-safe metric values into an active session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    session["metrics"].update(json.loads(json.dumps(metrics)))


def get_session_language_state(call_sid: str) -> dict[str, Any] | None:
    """Return the per-call language state, if the session exists."""

    session = ACTIVE_SESSIONS.get(call_sid)
    return session.get("language_state") if session is not None else None


def set_session_language_state(call_sid: str, state: dict[str, Any]) -> None:
    """Store a JSON-safe per-call language state."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    session["language_state"] = json.loads(json.dumps(state))


def get_session_memory(call_sid: str) -> dict[str, Any] | None:
    """Return the per-call bounded-memory state, if the session exists."""

    session = ACTIVE_SESSIONS.get(call_sid)
    return session.get("memory") if session is not None else None


def set_session_memory(call_sid: str, memory: dict[str, Any]) -> None:
    """Store a JSON-safe per-call bounded-memory state."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    session["memory"] = json.loads(json.dumps(memory))


def get_session_transcript(call_sid: str) -> list[dict[str, Any]]:
    """Return the chronological transcript events for an active session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    return list(session["transcript"]) if session is not None else []


def append_turn_record(call_sid: str, record: dict[str, Any]) -> None:
    """Append one finalized per-turn metrics record to the session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return
    records: list[dict[str, Any]] = session["metrics"].setdefault("turn_records", [])
    records.append(json.loads(json.dumps(record)))
    session["metrics"]["turn_count"] = len(records)


def append_session_transcript_event(
    call_sid: str,
    *,
    role: Literal["assistant", "user"],
    phase: Literal["greeting", "readiness", "task", "closing", "unknown"],
    text: str,
) -> None:
    """Append one chronological transcript event to the active session."""

    session = ACTIVE_SESSIONS.get(call_sid)
    if session is None:
        return

    transcript = session["transcript"]
    transcript.append(
        {
            "event_index": len(transcript) + 1,
            "role": role,
            "phase": phase,
            "text": text,
        }
    )
