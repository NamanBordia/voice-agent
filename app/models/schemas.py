"""Typed schemas for call-level and turn-level persistence data.

These Pydantic models define the persistence-safe shapes written to local JSON.
They are output schemas only — internal runtime containers remain as
plain dicts in app/runtime.py.

Transcript data is stored as chronological role-based events.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TranscriptEvent(BaseModel):
    """Single chronological transcript event.

    Supports assistant-only greetings and user-only readiness utterances.
    """

    event_index: int
    role: Literal["assistant", "user"]
    phase: Literal["greeting", "readiness", "task", "closing", "unknown"]
    text: str


class CallMetrics(BaseModel):
    """Aggregated metrics for an entire call.

    Accumulated during the call via runtime session helpers and
    finalized into this shape by finalize_call_log().
    """

    first_bot_speech_latency_secs: float | None = None
    first_bot_audio_started_at: float | None = None
    first_tts_frame_queued_at: float | None = None
    greeting_generation_started_at: float | None = None
    greeting_generation_completed_at: float | None = None
    greeting_llm_ttfb: float | None = None
    greeting_llm_total_latency: float | None = None
    greeting_text_ready_before_dial: bool | None = None
    greeting_text_source: str | None = None
    turn_count: int = 0
    # V3 per-turn records: "User Stop -> First Audio Sent" + stage timestamps.
    turn_records: list[dict[str, Any]] = Field(default_factory=list)


class CallLog(BaseModel):
    """Top-level persistence document for a completed call.

    The file envelope stores this payload without database-specific fields.
    Transcript uses role-based events so greetings can be stored without
    requiring a paired user turn.
    """

    call_sid: str
    stream_sid: str
    config_snapshot: dict[str, Any]  # JSON-safe ConfigSnapshot
    metrics: CallMetrics = Field(default_factory=CallMetrics)
    transcript: list[TranscriptEvent] = Field(default_factory=list)
    completed: bool = True
    error: str | None = None
    # Transport-level result (did the media session complete) vs. the derived
    # open-ended conversation outcome. See metrics_logger._derive_conversation_outcome:
    # completed | no_user_turns | greeting_failed | user_disconnected | pipeline_error.
    call_transport_status: str = "completed"
    conversation_outcome: str = "completed"
