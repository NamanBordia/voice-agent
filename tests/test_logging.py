from app.config import get_settings
from app.runtime import build_config_snapshot
from app.utils.metrics_logger import finalize_call_log


def test_finalize_call_log_includes_transcript():
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        metrics={
            "first_bot_speech_latency_secs": 0.9,
            "first_bot_audio_started_at": 2.0,
            "turn_count": 1,
            "turns": [],
        },
        transcript=[
            {
                "event_index": 1,
                "role": "user",
                "phase": "task",
                "text": "Hello",
            },
            {
                "event_index": 2,
                "role": "assistant",
                "phase": "task",
                "text": "Hi there",
            }
        ],
        completed=True,
        error=None,
    )

    assert call_log.metrics.turn_count == 1
    assert len(call_log.transcript) == 2
    assert call_log.transcript[0].event_index == 1
    assert call_log.transcript[0].text == "Hello"
    # Bot spoke (first_bot_audio_started_at set) + a user turn -> a healthy V3 call.
    assert call_log.conversation_outcome == "completed"
    assert call_log.call_transport_status == "completed"


def test_finalize_call_log_disconnect_is_user_disconnected_not_error():
    # A hang-up is signalled STRUCTURALLY (disconnected=True), not by error text.
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        metrics={"first_bot_audio_started_at": 2.0, "turn_count": 2},
        transcript=[],
        completed=True,
        error="User disconnected",
        disconnected=True,
    )

    assert call_log.completed is True
    assert call_log.error == "User disconnected"
    assert call_log.conversation_outcome == "user_disconnected"


def test_finalize_call_log_crash_text_with_disconnect_substring_is_pipeline_error():
    # REGRESSION (review MEDIUM): a real crash whose exception text merely CONTAINS
    # "disconnect" must NOT be misread as a hang-up — only the structured flag counts.
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        metrics={"first_bot_audio_started_at": 2.0, "turn_count": 1},
        transcript=[],
        completed=False,
        error="RuntimeError: remote disconnected by peer",
        disconnected=False,
    )
    assert call_log.conversation_outcome == "pipeline_error"


def test_finalize_call_log_outbound_fail_closed_is_greeting_failed():
    # REGRESSION (review LOW): the F2 fail-closed (pending_context_missing) is a
    # controlled greeting failure, not a runner crash, even though error is set.
    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"LLM_MODEL": "test-model"},
        metrics={"pending_context_missing": True, "greeting_text_source": "missing_outbound_context"},
        transcript=[],
        completed=False,
        error="outbound greeting missing (pending context lost)",
        disconnected=False,
    )
    assert call_log.conversation_outcome == "greeting_failed"


def test_finalize_call_log_accepts_v3_snapshot():
    snapshot = build_config_snapshot(get_settings())

    call_log = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot=snapshot,
        metrics={"first_bot_audio_started_at": 2.0, "turn_count": 3},
        transcript=[],
        completed=True,
        error=None,
    )

    assert call_log.config_snapshot["snapshot_version"] == "browser-v1"
    assert call_log.config_snapshot["runtime_config"]["llm"]["provider"] == "cerebras"
    assert call_log.conversation_outcome == "completed"


def test_finalize_call_log_outcome_taxonomy():
    snapshot = build_config_snapshot(get_settings())

    def _outcome(*, completed, error, metrics, disconnected=False):
        return finalize_call_log(
            call_sid="call_123",
            stream_sid="stream_456",
            config_snapshot=snapshot,
            metrics=metrics,
            transcript=[],
            completed=completed,
            error=error,
            disconnected=disconnected,
        ).conversation_outcome

    # Bot never produced audio -> greeting_failed.
    assert _outcome(completed=True, error=None, metrics={}) == "greeting_failed"
    # Bot greeted but no user turn -> no_user_turns.
    assert _outcome(
        completed=True, error=None, metrics={"first_bot_audio_started_at": 1.0, "turn_count": 0}
    ) == "no_user_turns"
    # Bot greeted + a user turn -> completed.
    assert _outcome(
        completed=True, error=None, metrics={"first_bot_audio_started_at": 1.0, "turn_count": 1}
    ) == "completed"
    # Runner raised a real exception -> pipeline_error.
    assert _outcome(
        completed=False, error="boom", metrics={"first_bot_audio_started_at": 1.0, "turn_count": 1}
    ) == "pipeline_error"
    # Structured disconnect flag -> user_disconnected (regardless of error text).
    assert _outcome(
        completed=True, error="User disconnected", metrics={}, disconnected=True
    ) == "user_disconnected"


def test_finalize_call_log_transport_status_reflects_completion():
    snapshot = build_config_snapshot(get_settings())

    failed = finalize_call_log(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot=snapshot,
        metrics={},
        transcript=[],
        completed=False,
        error="boom",
    )
    assert failed.call_transport_status == "failed"
    assert failed.conversation_outcome == "pipeline_error"
