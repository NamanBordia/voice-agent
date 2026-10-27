import pytest
from pydantic import ValidationError

from app.models.schemas import (
    CallLog,
    CallMetrics,
    TranscriptEvent,
)


def test_transcript_event_valid():
    event = TranscriptEvent(
        event_index=1,
        role="assistant",
        phase="greeting",
        text="Hi, is now a good time?",
    )
    assert event.event_index == 1
    assert event.role == "assistant"
    assert event.phase == "greeting"
    assert event.text == "Hi, is now a good time?"


def test_call_metrics_valid():
    metrics = CallMetrics(
        first_bot_speech_latency_secs=2.0,
        turn_count=1,
    )
    assert metrics.first_bot_speech_latency_secs == 2.0
    assert metrics.turn_count == 1
    assert metrics.turn_records == []

def test_call_log_valid():
    log = CallLog(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"model": "test"},
    )
    assert log.call_sid == "call_123"
    assert log.stream_sid == "stream_456"
    assert log.completed is True
    assert log.error is None
    assert log.conversation_outcome == "completed"
    assert isinstance(log.metrics, CallMetrics)
    assert log.transcript == []


def test_call_log_with_transcript():
    log = CallLog(
        call_sid="call_123",
        stream_sid="stream_456",
        config_snapshot={"model": "test"},
        transcript=[
            {
                "event_index": 1,
                "role": "assistant",
                "phase": "greeting",
                "text": "Hi there",
            }
        ],
    )
    assert len(log.transcript) == 1
    assert log.transcript[0].text == "Hi there"

def test_call_log_invalid_missing_fields():
    with pytest.raises(ValidationError):
        CallLog(call_sid="call_123")  # missing stream_sid and config_snapshot
