from app.config import get_settings
from app.runtime import ACTIVE_SESSIONS, append_session_transcript_event, create_session, delete_session, get_session, get_session_transcript


def test_append_session_transcript_event():
    settings = get_settings()
    create_session("call_123", "stream_456", settings)

    try:
        append_session_transcript_event(
            "call_123",
            role="assistant",
            phase="greeting",
            text="Hi, is now a good time?",
        )

        session = get_session("call_123")
        assert session is not None
        assert session["transcript"] == [
            {
                "event_index": 1,
                "role": "assistant",
                "phase": "greeting",
                "text": "Hi, is now a good time?",
            }
        ]
    finally:
        delete_session("call_123")
