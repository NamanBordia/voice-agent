"""Metrics logging helpers and call finalization.

Owns ``finalize_call_log()`` — converts runtime session state (the accumulated
metrics dict + transcript) into a persistence-safe ``CallLog`` — and the V3
open-ended conversation-outcome derivation.

Per-turn latency now lives entirely in the live ``turn_records`` path
(``TurnMetricsObserver`` -> ``append_turn_record``). The old
``LatencyBreakdown``/``TurnMetrics`` normalization was wired to an
``on_latency_breakdown`` observer callback that was never registered, so it has
been removed.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.models.schemas import CallLog, CallMetrics


# V3 is open-ended (no slot flow). Call outcomes are derived purely from
# signals the pipeline actually emits — the (completed, error) pair from
# PipelineExecutionResult plus per-call metrics — so a healthy completed call
# reads as "completed" instead of the old slot-era "incomplete".
CONVERSATION_OUTCOMES = (
    "completed",          # bot greeted, >=1 user turn, no error
    "no_user_turns",      # bot greeted but the caller never took a turn
    "greeting_failed",    # the bot never produced any audio
    "user_disconnected",  # caller hung up — a normal end, not an error
    "pipeline_error",     # the runner raised / aborted
)


def _derive_conversation_outcome(
    *,
    completed: bool,
    error: str | None,
    disconnected: bool,
    metrics: dict[str, Any],
) -> str:
    """Map (completed, error, disconnected, metrics) onto a V3 open-ended outcome.

    A hang-up is signalled STRUCTURALLY by ``disconnected`` (never by substring-
    matching arbitrary exception text, which would misread a real crash as a
    hang-up). A controlled outbound fail-closed (``pending_context_missing``) is a
    greeting failure, not a runner crash. Otherwise any error / non-completion is a
    ``pipeline_error``; a clean completion is then refined by whether the bot ever
    spoke (``first_bot_audio_started_at``) and whether the caller took a turn.
    """

    if disconnected:
        return "user_disconnected"
    if metrics.get("pending_context_missing"):
        # Outbound fail-closed: the LLM greeting was missing so the bot never spoke.
        # A controlled greeting failure, not a crash (the runner never even started).
        return "greeting_failed"
    if error or not completed:
        return "pipeline_error"

    if metrics.get("first_bot_audio_started_at") is None:
        return "greeting_failed"
    if (metrics.get("turn_count") or 0) < 1:
        return "no_user_turns"
    return "completed"


def _conversation_summary(
    *,
    completed: bool,
    error: str | None,
    disconnected: bool,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Transport status + derived V3 conversation outcome for finalize_call_log."""

    return {
        "call_transport_status": "completed" if completed else "failed",
        "conversation_outcome": _derive_conversation_outcome(
            completed=completed, error=error, disconnected=disconnected, metrics=metrics
        ),
    }


def finalize_call_log(
    *,
    call_sid: str,
    stream_sid: str,
    config_snapshot: dict[str, Any],
    metrics: dict[str, Any],
    transcript: list[dict[str, Any]],
    completed: bool,
    error: str | None,
    disconnected: bool = False,
) -> CallLog:
    """Build a persistence-safe CallLog from runtime session state.

    Converts the runtime session's accumulated metrics dict into a
    typed CallLog ready for local JSON serialization.

    Args:
        call_sid: Local conversation identifier.
        stream_sid: Local audio-stream identifier.
        config_snapshot: The immutable per-call config snapshot.
        metrics: The accumulated metrics dict from the runtime session.
        transcript: The completed transcript turns from the runtime session.
        completed: Whether the pipeline completed normally.
        error: Error description if the pipeline failed.

    Returns:
        A CallLog ready for persist_call_log().
    """

    log = logger.bind(call_sid=call_sid)

    # Reconstruct typed CallMetrics from the accumulated dict
    call_metrics = CallMetrics(
        first_bot_speech_latency_secs=metrics.get("first_bot_speech_latency_secs"),
        first_bot_audio_started_at=metrics.get("first_bot_audio_started_at"),
        first_tts_frame_queued_at=metrics.get("first_tts_frame_queued_at"),
        greeting_generation_started_at=metrics.get("greeting_generation_started_at"),
        greeting_generation_completed_at=metrics.get("greeting_generation_completed_at"),
        greeting_llm_ttfb=metrics.get("greeting_llm_ttfb"),
        greeting_llm_total_latency=metrics.get("greeting_llm_total_latency"),
        greeting_text_ready_before_dial=metrics.get("greeting_text_ready_before_dial"),
        greeting_text_source=metrics.get("greeting_text_source"),
        turn_count=metrics.get("turn_count", 0),
        turn_records=metrics.get("turn_records", []),
    )
    conversation_summary = _conversation_summary(
        completed=completed,
        error=error,
        disconnected=disconnected,
        metrics=metrics,
    )

    call_log = CallLog(
        call_sid=call_sid,
        stream_sid=stream_sid,
        config_snapshot=config_snapshot,
        metrics=call_metrics,
        transcript=transcript,
        completed=completed,
        error=error,
        **conversation_summary,
    )

    log.info(
        "Call log finalized",
        turn_count=call_metrics.turn_count,
        first_latency=call_metrics.first_bot_speech_latency_secs,
    )

    return call_log
