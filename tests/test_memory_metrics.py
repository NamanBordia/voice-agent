"""Per-turn memory feature: recall-with-sources + observer snapshot-at-promote."""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from app.services.call_metrics import TurnMetricsObserver
from app.services.memory import search_recall_snippets, search_recall_with_sources

_REC = {
    "turn_id": 1, "user_text": "hello", "assistant_text": "hi",
    "detected_language": "en", "response_language": "en",
    "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
    "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
    "llm_response_started_at": 100.4, "llm_first_token_or_text_at": 100.7,
    "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
    "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
}


def _push(obs, frame):
    asyncio.run(obs.on_push_frame(FramePushed(
        source=None, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )))


def test_memory_marks_attached_via_provider():
    out: list[dict] = []
    marks = {
        "recall_used": True,
        "recall_snippets": ["user: I work at Acme"],
        "recall_source_turns": [1],
        "pinned_facts_count": 2,
        "approx_memory_tokens": 120,
    }
    obs = TurnMetricsObserver(on_record=out.append, memory_marks_provider=lambda: marks)
    obs._emit(dict(_REC))
    assert out[0]["memory"]["recall_used"] is True
    assert out[0]["memory"]["recall_source_turns"] == [1]
    assert "_memory_marks_snapshot" not in out[0]  # internal key stripped


def test_no_memory_provider_adds_no_memory_key():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)  # no provider
    obs._emit(dict(_REC))
    assert "memory" not in out[0]  # record shape unchanged when feature absent


def test_empty_memory_marks_adds_no_memory_key():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append, memory_marks_provider=lambda: {})
    obs._emit(dict(_REC))
    assert "memory" not in out[0]  # empty snapshot -> no key (e.g. greeting-only turn)


def test_memory_marks_snapshotted_at_promote_survive_next_turn_reset():
    # REGRESSION: marks are pinned at the record's own Start frame (promote), so a
    # next-turn _govern_context that replaces the live marks BEFORE finalize cannot
    # mislabel this turn's memory. Mirrors the speculation snapshot-at-promote test.
    out: list[dict] = []
    live = {"recall_used": True, "recall_snippets": ["user: earlier thing"], "recall_source_turns": [2]}
    obs = TurnMetricsObserver(on_record=out.append, memory_marks_provider=lambda: live)

    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="hi", stt_eot_received_at=100.3, turn_committed_at=100.35)
    _push(obs, LLMFullResponseStartFrame())   # promote -> snapshot captures turn-1 live marks
    live.clear()                               # simulate next turn's _govern_context wiping marks
    _push(obs, LLMTextFrame("<en> Hi"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Hi")
    _push(obs, BotStartedSpeakingFrame())      # -> finalize

    assert len(out) == 1
    mem = out[0]["memory"]
    assert mem["recall_used"] is True                      # from the promote-time snapshot
    assert mem["recall_snippets"] == ["user: earlier thing"]
    assert mem["recall_source_turns"] == [2]
    assert "_memory_marks_snapshot" not in out[0]


def test_flushed_no_bot_response_turns_keep_own_memory_marks():
    # REGRESSION (review LOW): two user turns queued with no bot response, flushed at
    # call end. Each must carry the marks captured at ITS note_user_turn, not the live
    # provider's last-turn marks (the never-promoted flush path borrowed them before).
    out: list[dict] = []
    live = {"recall_used": True, "recall_snippets": ["turn1"], "tag": "A"}
    obs = TurnMetricsObserver(on_record=out.append, memory_marks_provider=lambda: live)

    obs.note_user_turn(turn_id=1, user_text="alpha", stt_eot_received_at=1.0, turn_committed_at=1.0)
    live = {"recall_used": False, "recall_snippets": ["turn2"], "tag": "B"}  # provider now sees turn 2
    obs.note_user_turn(turn_id=2, user_text="beta", stt_eot_received_at=2.0, turn_committed_at=2.0)
    _push(obs, EndFrame())  # flush both as no_bot_response

    assert len(out) == 2
    assert out[0]["turn_id"] == 1 and out[0]["memory"]["tag"] == "A"   # turn 1 keeps its own marks
    assert out[1]["turn_id"] == 2 and out[1]["memory"]["tag"] == "B"   # turn 2 keeps its own marks
    assert all(r["error_stage"] == "no_bot_response" for r in out)


def test_search_recall_with_sources_returns_event_index():
    transcript = [
        {"event_index": 1, "role": "user", "text": "I work at Acme Corporation in Bangalore"},
        {"event_index": 2, "role": "assistant", "text": "Great, noted that down."},
        {"event_index": 3, "role": "user", "text": "filler chit chat here"},
        {"event_index": 4, "role": "user", "text": "what did i say about Acme"},
    ]
    results = search_recall_with_sources(
        transcript, query="remind me about Acme Corporation Bangalore",
        max_snippets=2, min_overlap=1,
    )
    assert results
    assert results[0]["source_event_index"] == 1
    assert "Acme" in results[0]["text"]
    # The plain wrapper returns exactly the snippet strings, unchanged behavior.
    plain = search_recall_snippets(
        transcript, query="remind me about Acme Corporation Bangalore",
        max_snippets=2, min_overlap=1,
    )
    assert plain == [r["text"] for r in results]


def test_search_recall_with_sources_handles_missing_event_index():
    # Legacy events without event_index -> source is None, snippet still returned.
    transcript = [
        {"role": "user", "text": "the budget is forty thousand rupees monthly"},
        {"role": "user", "text": "what was the budget again"},
    ]
    results = search_recall_with_sources(
        transcript, query="budget rupees monthly", max_snippets=1, min_overlap=1,
    )
    assert results
    assert results[0]["source_event_index"] is None
