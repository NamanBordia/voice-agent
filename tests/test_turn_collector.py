from datetime import datetime, timedelta, timezone

from app.models.conversation_models import TurnEvent
from app.services.turn_collector import TurnCollector


BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _event(
    event_type: str,
    text: str | None = None,
    *,
    seconds: float = 0,
    metadata: dict | None = None,
) -> TurnEvent:
    return TurnEvent(
        event_type=event_type,
        text=text,
        timestamp=BASE_TIME + timedelta(seconds=seconds),
        metadata=metadata or {},
    )


def test_end_of_turn_commits_once():
    collector = TurnCollector(call_sid="call_123", stream_sid="stream_456")

    result = collector.observe(
        _event("end_of_turn", "Yes, please go ahead."),
        phase="readiness",
    )

    assert result.turn is not None
    assert result.turn.turn_index == 1
    assert result.turn.normalized_text == "Yes, please go ahead."
    assert result.turn.source == "deepgram_flux_end_of_turn"
    assert result.turn.model_dump(mode="json")["stream_sid"] == "stream_456"


def test_final_transcript_alone_does_not_commit():
    collector = TurnCollector(call_sid="call_123")

    result = collector.observe(
        _event("final_transcript", "This should only buffer."),
        phase="task",
    )

    assert result.turn is None
    assert result.ignored_reason is None


def test_buffered_final_transcript_commits_when_eot_arrives_without_text():
    collector = TurnCollector(call_sid="call_123")
    collector.observe(_event("final_transcript", "Buffered final text."), phase="task")

    result = collector.observe(_event("end_of_turn", ""), phase="task")

    assert result.turn is not None
    assert result.turn.normalized_text == "Buffered final text."


def test_eot_callback_text_wins_over_buffered_final_text():
    collector = TurnCollector(call_sid="call_123")
    collector.observe(_event("final_transcript", "Older buffered text."), phase="task")

    result = collector.observe(
        _event("end_of_turn", "Authoritative callback text."),
        phase="task",
    )

    assert result.turn is not None
    assert result.turn.normalized_text == "Authoritative callback text."
    assert result.turn.provider_metadata["buffered_final_text_mismatch"] == "Older buffered text."


def test_eot_callback_first_or_alone_commits_with_callback_text():
    collector = TurnCollector(call_sid="call_123")

    result = collector.observe(
        _event("end_of_turn", "Callback only transcript."),
        phase="task",
    )

    assert result.turn is not None
    assert result.turn.normalized_text == "Callback only transcript."


def test_empty_and_noise_text_is_ignored():
    collector = TurnCollector(call_sid="call_123")

    result = collector.observe(_event("end_of_turn", "?! ..."), phase="task")

    assert result.turn is None
    assert result.ignored_reason == "empty_text"


def test_eager_end_of_turn_and_turn_resumed_do_not_commit():
    collector = TurnCollector(call_sid="call_123")

    eager = collector.observe(_event("eager_end_of_turn", "Maybe done"), phase="task")
    resumed = collector.observe(_event("turn_resumed"), phase="task")

    assert eager.turn is None
    assert resumed.turn is None


def test_greeting_pending_suppresses_and_clears_buffer():
    collector = TurnCollector(call_sid="call_123")
    collector.observe(
        _event("final_transcript", "Hello over greeting."),
        phase="greeting",
        greeting_pending=True,
    )

    result = collector.observe(_event("end_of_turn", ""), phase="readiness")

    assert result.turn is None
    assert result.ignored_reason == "empty_text"


def test_provider_turn_index_is_used_when_available():
    collector = TurnCollector(call_sid="call_123")

    result = collector.observe(
        _event("end_of_turn", "My answer.", metadata={"turn_index": "7"}),
        phase="task",
    )

    assert result.provider_turn_index == 7


def test_duplicate_suppression_is_narrow():
    collector = TurnCollector(call_sid="call_123")

    first = collector.observe(_event("end_of_turn", "yes", seconds=0), phase="task")
    duplicate = collector.observe(_event("end_of_turn", "yes", seconds=0.1), phase="task")
    repeated_later = collector.observe(_event("end_of_turn", "yes", seconds=1), phase="task")

    assert first.turn is not None
    assert duplicate.turn is None
    assert duplicate.ignored_reason == "duplicate_turn"
    assert duplicate.duplicate_suppressed is True
    assert repeated_later.turn is not None
    assert repeated_later.turn.turn_index == 2


def test_buffer_clears_after_commit_so_next_eot_cannot_reuse_stale_text():
    collector = TurnCollector(call_sid="call_123")
    collector.observe(_event("final_transcript", "Turn one text.", seconds=0), phase="task")

    first = collector.observe(_event("end_of_turn", "", seconds=0.1), phase="task")
    second = collector.observe(_event("end_of_turn", "", seconds=1), phase="task")

    assert first.turn is not None
    assert first.turn.normalized_text == "Turn one text."
    assert second.turn is None
    assert second.ignored_reason == "empty_text"


def test_late_final_transcript_after_eot_first_commit_does_not_become_stale_buffer():
    collector = TurnCollector(call_sid="call_123")

    first = collector.observe(_event("end_of_turn", "Callback first text.", seconds=0), phase="task")
    late_final = collector.observe(
        _event("final_transcript", "Callback first text.", seconds=0.1),
        phase="task",
    )
    second = collector.observe(_event("end_of_turn", "", seconds=1), phase="task")

    assert first.turn is not None
    assert late_final.turn is None
    assert late_final.duplicate_suppressed is True
    assert second.turn is None
    assert second.ignored_reason == "empty_text"


def test_late_different_final_after_callback_first_commit_does_not_reuse_stale_text():
    collector = TurnCollector(call_sid="call_123")

    first = collector.observe(_event("end_of_turn", "Callback first text.", seconds=0), phase="task")
    late_final = collector.observe(
        _event("final_transcript", "Different final text.", seconds=0.1),
        phase="task",
    )
    second = collector.observe(_event("end_of_turn", "", seconds=1), phase="task")

    assert first.turn is not None
    assert first.turn.normalized_text == "Callback first text."
    assert late_final.turn is None
    assert late_final.ignored_reason == "late_final_after_commit"
    assert second.turn is None
    assert second.ignored_reason == "empty_text"
