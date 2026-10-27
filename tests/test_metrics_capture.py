import asyncio

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSStartedFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from app.services.call_metrics import TurnMetricsObserver, _ms

_BREAKDOWN_REC = {
    "turn_id": 1, "user_text": "hello", "assistant_text": "hi there",
    "detected_language": "en", "response_language": "en",
    "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
    "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
    "llm_response_started_at": 100.4, "llm_first_token_or_text_at": 100.7,
    "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
    "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
}


def _push(obs, frame):
    data = FramePushed(
        source=None, destination=None, frame=frame,
        direction=FrameDirection.DOWNSTREAM, timestamp=0,
    )
    asyncio.run(obs.on_push_frame(data))


def test_assistant_text_is_parser_fed_not_frame_scraped():
    # The raw LLM text carries the <en>/<hi> tag; assistant_text must come from
    # note_assistant_text (the prefix processor), never from LLMTextFrame.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="hello", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> Hi"))          # tagged raw text -> timing only
    _push(obs, LLMFullResponseEndFrame())
    obs.note_language(detected_language="en", response_language="en")
    obs.note_assistant_text("Hi there!")          # clean spoken text
    _push(obs, TTSStartedFrame())
    _push(obs, BotStartedSpeakingFrame())

    assert len(out) == 1
    rec = out[0]
    assert rec["assistant_text"] == "Hi there!"
    assert "<en>" not in rec["assistant_text"]
    assert rec["turn_id"] == 1
    assert rec["response_language"] == "en"
    assert rec["llm_first_token_or_text_at"] is not None
    assert rec["primary_latency_ms"] is not None


def test_two_eots_before_reply_merge_without_losing_ids():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=3, user_text="first", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=4, user_text="second", stt_eot_received_at=2.0, turn_committed_at=2.0)
    # A single bot response for both queued user turns.
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> ok"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Got it.")
    _push(obs, BotStartedSpeakingFrame())

    assert len(out) == 1
    rec = out[0]
    assert rec["turn_id"] == 3
    assert rec["merged_user_turn_ids"] == [3, 4]
    assert "first" in rec["user_text"] and "second" in rec["user_text"]
    assert rec["assistant_text"] == "Got it."
    assert rec["primary_latency_ms"] is not None


def test_user_stopped_falls_back_to_eot_when_no_vad_frame():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=5.0, turn_committed_at=5.1)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("ok")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["user_stopped_speaking_at"] == 5.0
    assert out[0]["user_stopped_is_eot_fallback"] is True
    assert out[0]["primary_latency_ms"] is not None


def test_vad_stopped_frame_anchors_at_timestamp_minus_stop_secs():
    # F1: the VAD true-silence instant = frame.timestamp - stop_secs, captured on
    # VADUserStoppedSpeakingFrame and consumed by the next note_user_turn. The EOT
    # follows the VAD silence (realistic ordering), so the anchor is accepted.
    obs = TurnMetricsObserver(on_record=lambda r: None)
    frame = VADUserStoppedSpeakingFrame(stop_secs=0.2)
    frame.timestamp = 100.0
    _push(obs, frame)
    assert obs._pending_vad_user_stopped_at == pytest.approx(99.8)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=100.4, turn_committed_at=100.5)
    assert obs._pending_turns[0]["user_stopped_speaking_vad_at"] == pytest.approx(99.8)
    assert obs._pending_vad_user_stopped_at is None  # consumed


def test_vad_anchor_rejected_when_after_eot():
    # F1 guard: an anchor that post-dates the EOT can't belong to this turn.
    obs = TurnMetricsObserver(on_record=lambda r: None)
    frame = VADUserStoppedSpeakingFrame(stop_secs=0.0)
    frame.timestamp = 105.0
    _push(obs, frame)  # anchor = 105.0
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=100.0, turn_committed_at=100.1)
    assert obs._pending_turns[0]["user_stopped_speaking_vad_at"] is None


def test_vad_anchor_rejected_when_implausibly_old_for_this_turn():
    # F1 guard: a stale anchor from a prior utterance (muted barge-in leaves no fresh
    # VADUserStartedSpeakingFrame to clear it) must not attach to a much-later turn.
    obs = TurnMetricsObserver(on_record=lambda r: None)
    frame = VADUserStoppedSpeakingFrame(stop_secs=0.2)
    frame.timestamp = 100.0
    _push(obs, frame)  # stale anchor = 99.8
    # Next turn's EOT is 100s later -> the leftover anchor is rejected as stale.
    obs.note_user_turn(turn_id=2, user_text="y", stt_eot_received_at=200.0, turn_committed_at=200.1)
    assert obs._pending_turns[0]["user_stopped_speaking_vad_at"] is None


def test_vad_started_frame_clears_stale_anchor():
    # A fresh utterance invalidates any earlier VAD-stop anchor (no cross-turn bleed).
    obs = TurnMetricsObserver(on_record=lambda r: None)
    frame = VADUserStoppedSpeakingFrame(stop_secs=0.2)
    frame.timestamp = 100.0
    _push(obs, frame)
    _push(obs, VADUserStartedSpeakingFrame())
    assert obs._pending_vad_user_stopped_at is None


def test_vad_anchor_is_headline_when_present():
    # F1: VAD anchor (earlier than turn-release) becomes the headline primary latency.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = dict(_BREAKDOWN_REC)
    rec["user_stopped_speaking_at"] = 100.0      # turn-release anchor
    rec["user_stopped_speaking_vad_at"] = 99.7   # VAD true-silence (earlier)
    obs._emit(rec)
    r = out[0]
    assert r["primary_latency_anchor"] == "vad"
    assert r["user_stop_to_first_audio_vad_ms"] == round((101.2 - 99.7) * 1000, 1)      # 1500
    assert r["user_stop_to_first_audio_release_ms"] == round((101.2 - 100.0) * 1000, 1)  # 1200
    # The headline equals the VAD value and is LARGER (more honest) than turn-release.
    assert r["primary_latency_ms"] == r["user_stop_to_first_audio_vad_ms"]
    assert r["user_stop_to_first_audio_vad_ms"] > r["user_stop_to_first_audio_release_ms"]


def test_primary_latency_falls_back_to_release_anchor_without_vad():
    # No VAD frame -> headline uses the turn-release anchor; existing fields unchanged.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs._emit(dict(_BREAKDOWN_REC))  # no user_stopped_speaking_vad_at key
    r = out[0]
    assert r["primary_latency_anchor"] == "turn_release"
    assert r["user_stop_to_first_audio_vad_ms"] is None
    assert r["user_stop_to_first_audio_ms"] == r["user_stop_to_first_audio_release_ms"]
    assert r["primary_latency_ms"] == round((101.2 - 100.0) * 1000, 1)


def test_language_resolved_at_finalize_from_provider():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append, language_provider=lambda: ("hi", "hi"))
    obs.note_user_turn(turn_id=1, user_text="haan", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("theek hai")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["detected_language"] == "hi"
    assert out[0]["response_language"] == "hi"


def test_ms_helper():
    assert _ms(1.5, 1.0) == 500.0
    assert _ms(None, 1.0) is None
    assert _ms(1.0, None) is None


def test_emit_computes_breakdown():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "hello", "assistant_text": "hi there",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
        "llm_response_started_at": 100.4, "llm_first_token_or_text_at": 100.7,
        "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
        "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["primary_latency_ms"] == round((101.2 - 100.0) * 1000, 1)
    assert r["eot_delay_ms"] == round((100.3 - 100.0) * 1000, 1)
    assert r["commit_delay_ms"] == round((100.35 - 100.3) * 1000, 1)
    assert r["llm_ttfb_ms"] == round((100.7 - 100.4) * 1000, 1)
    assert r["llm_total_ms"] == round((101.0 - 100.4) * 1000, 1)
    assert r["tts_first_audio_ms"] == round((101.2 - 101.05) * 1000, 1)
    # New stage-breakdown deltas (the segments that localize a slow turn).
    assert r["user_stop_to_eot_ms"] == round((100.3 - 100.0) * 1000, 1)
    assert r["eot_to_turn_committed_ms"] == round((100.35 - 100.3) * 1000, 1)
    assert r["user_stop_to_llm_start_ms"] == round((100.4 - 100.0) * 1000, 1)
    assert r["turn_committed_to_llm_start_ms"] == round((100.4 - 100.35) * 1000, 1)
    assert r["llm_end_to_tts_start_ms"] == round((101.05 - 101.0) * 1000, 1)
    assert r["user_stop_to_first_audio_ms"] == round((101.2 - 100.0) * 1000, 1)
    assert r["earliest_user_stop_to_first_audio_ms"] == r["user_stop_to_first_audio_ms"]
    assert r["latest_user_stop_to_first_audio_ms"] == r["user_stop_to_first_audio_ms"]


def test_delayed_llm_start_surfaces_in_committed_to_llm_start():
    # A 12s gap between turn-commit and the LLM starting must be visible as the
    # committed_to_llm_start / user_stop_to_llm_start deltas, NOT hidden.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 10.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 10.001, "turn_committed_at": 10.002,
        "llm_response_started_at": 22.0, "llm_first_token_or_text_at": 22.5,
        "llm_completed_at": 22.6, "tts_request_started_at": 22.63,
        "first_audio_frame_sent_at": 22.7, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["turn_committed_to_llm_start_ms"] == round((22.0 - 10.002) * 1000, 1)  # ~11998
    assert r["user_stop_to_llm_start_ms"] == round((22.0 - 10.0) * 1000, 1)         # 12000
    assert r["llm_ttfb_ms"] == round((22.5 - 22.0) * 1000, 1)                       # 500 (LLM was fine)


def test_merged_turn_stores_earliest_latest_and_subturns():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=3, user_text="Hello.", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=4, user_text="Computer science.", stt_eot_received_at=2.0, turn_committed_at=2.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> ok"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Got it.")
    _push(obs, BotStartedSpeakingFrame())

    rec = out[0]
    assert rec["merged_user_turn_ids"] == [3, 4]
    assert rec["merged_user_texts"] == ["Hello.", "Computer science."]
    assert len(rec["merged_user_stop_times"]) == 2
    assert rec["merged_turn_commit_times"] == [1.0, 2.0]
    assert rec["earliest_user_stop_to_first_audio_ms"] is not None
    assert rec["latest_user_stop_to_first_audio_ms"] is not None
    # The latest sub-stop is later, so its latency is <= the earliest-anchored one.
    assert rec["latest_user_stop_to_first_audio_ms"] <= rec["earliest_user_stop_to_first_audio_ms"]
    # Latest commit anchor (2.0) is later than earliest (1.0), so the latest
    # Commit->LLM-start (the real backend delay) is the smaller number (LAT-1).
    assert rec["latest_turn_committed_to_llm_start_ms"] < rec["earliest_turn_committed_to_llm_start_ms"]


def test_merged_turn_earliest_and_latest_commit_to_llm_start():
    # LAT-1: earliest commit (first sub-utterance) inflates Commit->LLM start by
    # the inter-utterance pause; the latest commit gives the real backend delay.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 3, "user_text": "a\nb", "assistant_text": "ok",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 1.0, "latest_user_stopped_speaking_at": 9.0,
        "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 1.0, "turn_committed_at": 1.0,
        "earliest_turn_committed_at": 1.0, "latest_turn_committed_at": 9.0,
        "merged_user_turn_ids": [3, 4],
        "llm_response_started_at": 10.0, "llm_first_token_or_text_at": 10.4,
        "llm_completed_at": 10.5, "tts_request_started_at": 10.55,
        "first_audio_frame_sent_at": 10.6, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["earliest_turn_committed_to_llm_start_ms"] == round((10.0 - 1.0) * 1000, 1)  # 9000 (inflated)
    assert r["latest_turn_committed_to_llm_start_ms"] == round((10.0 - 9.0) * 1000, 1)    # 1000 (real)
    assert r["earliest_user_stop_to_first_audio_ms"] == round((10.6 - 1.0) * 1000, 1)
    assert r["latest_user_stop_to_first_audio_ms"] == round((10.6 - 9.0) * 1000, 1)


def test_normal_turn_latest_commit_equals_committed_to_llm_start():
    # For a non-merged turn, earliest==latest==turn_committed_to_llm_start_ms, so
    # the UI's latest-anchored value is always correct for normal turns too.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.0, "turn_committed_at": 100.0,
        "llm_response_started_at": 100.5, "llm_first_token_or_text_at": 100.9,
        "llm_completed_at": 101.0, "tts_request_started_at": 101.05,
        "first_audio_frame_sent_at": 101.1, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["turn_committed_to_llm_start_ms"] == round((100.5 - 100.0) * 1000, 1)
    assert r["latest_turn_committed_to_llm_start_ms"] == r["turn_committed_to_llm_start_ms"]
    assert r["earliest_turn_committed_to_llm_start_ms"] == r["turn_committed_to_llm_start_ms"]


def test_negative_llm_end_to_tts_is_preserved_not_clamped():
    # MET-1: under streaming, TTS can start before the LLM end frame -> the metric
    # is legitimately negative (overlap). It must be PRESERVED, never clamped, so a
    # real (large positive) hand-off stall stays visible.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.0, "turn_committed_at": 100.0,
        "llm_response_started_at": 100.5, "llm_first_token_or_text_at": 100.9,
        "llm_completed_at": 101.0, "tts_request_started_at": 100.99,  # TTS started before LLM end
        "first_audio_frame_sent_at": 101.1, "error_stage": None, "error_message": None,
    }
    obs._emit(rec)
    r = out[0]
    assert r["llm_end_to_tts_start_ms"] == round((100.99 - 101.0) * 1000, 1)
    assert r["llm_end_to_tts_start_ms"] < 0


def test_bot_was_speaking_when_user_stopped_flag():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    _push(obs, BotStartedSpeakingFrame())   # bot is speaking
    _push(obs, UserStoppedSpeakingFrame())  # ...user stops while it speaks
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    assert obs._pending_turns[0]["bot_was_speaking_when_user_stopped"] is True


def test_bot_not_speaking_when_user_stopped_flag():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    _push(obs, BotStartedSpeakingFrame())
    _push(obs, BotStoppedSpeakingFrame())   # bot finished
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    assert obs._pending_turns[0]["bot_was_speaking_when_user_stopped"] is False


def test_memory_duration_attached_to_turn():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_memory_timing(duration_ms=12.5, context_message_count=7)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("ok")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["memory_processor_duration_ms"] == 12.5
    assert out[0]["context_message_count"] == 7


def test_note_provider_error_sets_stage_when_open_and_unset():
    # F6: a TTS/STT failure mid-turn gets a stage-specific label on the open record.
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())   # opens the record
    obs.note_provider_error(stage="tts_provider_error", message="tts_provider_error: cartesia 500")
    assert obs._open["error_stage"] == "tts_provider_error"
    assert "cartesia 500" in obs._open["error_message"]


def test_note_provider_error_does_not_overwrite_existing_stage():
    # The parser's specific llm_no_output must win over the generic provider label.
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    obs.note_error(stage="llm_no_output", message="empty")
    obs.note_provider_error(stage="llm_provider_error", message="429")
    assert obs._open["error_stage"] == "llm_no_output"


def test_note_provider_error_noops_without_open_record():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs.note_provider_error(stage="stt_provider_error", message="deepgram down")
    assert obs._open is None  # no crash, nothing attributed (falls through to call-end)


def test_stt_ready_flag_persisted():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0, stt_ready=False)
    assert obs._pending_turns[0]["stt_ready_before_user_turn"] is False


def test_note_user_turn_queues_pending_not_open():
    obs = TurnMetricsObserver(on_record=lambda r: None)
    obs._pending_user_stopped_at = 50.0
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=50.2, turn_committed_at=50.25)
    assert obs._open is None
    assert len(obs._pending_turns) == 1
    assert obs._pending_turns[0]["user_stopped_speaking_at"] == 50.0
    assert obs._pending_user_stopped_at is None


def test_flush_emits_pending_turn_on_call_end():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, EndFrame())
    assert len(out) == 1
    assert out[0]["error_stage"] == "no_bot_response"


def _emit_with_probe(probe, **overrides):
    """Run _emit on a minimal rec carrying an eager probe; return the finalized probe."""
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    rec = {
        "turn_id": 1, "user_text": "x", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
        "llm_response_started_at": 100.5, "llm_first_token_or_text_at": 101.0,  # ttfb = 500ms
        "llm_completed_at": 101.1, "tts_request_started_at": 101.15,
        "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
        "eager_probe": probe,
    }
    rec.update(overrides)
    obs._emit(rec)
    return out[0]["eager_probe"]


def test_eager_probe_formula_and_eligible_verdict():
    # eager fired 300ms before EOT; eager==final; no resume. ttfb=500ms.
    #   hypothetical_spec_first_token = 100.0 + 0.5 = 100.5
    #   effective_ready = max(100.3, 100.5) = 100.5
    #   estimated_saved = (101.0 - 100.5) * 1000 = 500ms
    ep = _emit_with_probe({
        "eager_eot_seen": True, "eager_eot_at": 100.0, "eager_transcript": "hello world",
        "turn_resumed_seen": False, "turn_resumed_at": None, "resumed_since_eager": False,
        "final_eot_at": 100.3, "final_transcript": "hello world",
    })
    assert ep["head_start_ms"] == 300.0
    assert ep["eager_final_match"] is True
    assert ep["estimated_saved_ms"] == 500.0
    assert ep["verdict"] == "eligible"
    assert ep["eager_transcript_preview"] == "hello world"
    # full transcripts are dropped (only previews persist)
    assert "eager_transcript" not in ep and "final_transcript" not in ep


def test_eager_probe_verdicts():
    base = {
        "eager_eot_seen": True, "eager_eot_at": 100.0, "eager_transcript": "hello world",
        "turn_resumed_seen": False, "turn_resumed_at": None, "resumed_since_eager": False,
        "final_eot_at": 100.3, "final_transcript": "hello world",
    }
    # TurnResumed -> wasted, 0 saved
    ep = _emit_with_probe({**base, "turn_resumed_seen": True, "resumed_since_eager": True})
    assert ep["verdict"] == "wasted_turn_resumed" and ep["estimated_saved_ms"] == 0.0
    # eager != final -> mismatch, 0 saved
    ep = _emit_with_probe({**base, "final_transcript": "totally different"})
    assert ep["verdict"] == "discarded_transcript_mismatch" and ep["estimated_saved_ms"] == 0.0
    assert ep["eager_final_match"] is False
    # eager never fired -> no_eager_event, saved None
    ep = _emit_with_probe({"eager_eot_seen": False})
    assert ep["verdict"] == "no_eager_event" and ep["estimated_saved_ms"] is None
    # head start <= 0 (eager at == final at)
    ep = _emit_with_probe({**base, "eager_eot_at": 100.3})
    assert ep["verdict"] == "not_enough_head_start"
    # eligible but normal LLM produced no first token -> unmeasurable
    ep = _emit_with_probe(base, llm_first_token_or_text_at=None)
    assert ep["verdict"] == "eligible_unmeasurable" and ep["estimated_saved_ms"] is None


def test_merged_turn_keeps_latest_eager_probe():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=3, user_text="first", stt_eot_received_at=1.0, turn_committed_at=1.0,
                       eager_probe={"eager_eot_seen": True, "eager_eot_at": 0.7, "eager_transcript": "first"})
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=4, user_text="second", stt_eot_received_at=2.0, turn_committed_at=2.0,
                       eager_probe={"eager_eot_seen": True, "eager_eot_at": 1.8, "eager_transcript": "second"})
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> ok"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("Got it.")
    _push(obs, BotStartedSpeakingFrame())
    ep = out[0]["eager_probe"]
    assert ep["eager_transcript_preview"] == "second"   # latest sub-turn's probe wins
    assert ep["final_transcript_preview"] == "second"


def test_disabled_eager_adds_no_probe_key():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)  # no eager_probe
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("ok")
    _push(obs, BotStartedSpeakingFrame())
    assert "eager_probe" not in out[0]   # record shape unchanged when telemetry off


def test_response_id_is_stamped_on_record():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="a", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, LLMTextFrame("<en> hi"))
    _push(obs, LLMFullResponseEndFrame())
    obs.note_assistant_text("hi")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["response_id"] == 1


def test_stray_late_llm_end_does_not_corrupt_active_response():
    # A late End from a superseded response arrives before the active response has
    # produced any text -> ignored, so llm_completed_at stays None (no bogus 3ms).
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="a", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())     # open, response_id=1
    _push(obs, LLMFullResponseEndFrame())       # stray End: no first-text yet -> ignored
    assert obs._open["llm_completed_at"] is None
    assert not obs._open.get("_llm_ended")
    # The real frames for this response then arrive and ARE accepted.
    _push(obs, LLMTextFrame("<en> hi"))
    _push(obs, LLMFullResponseEndFrame())
    assert obs._open["llm_completed_at"] is not None
    obs.note_assistant_text("hi")
    _push(obs, BotStartedSpeakingFrame())
    assert out[0]["llm_total_ms"] is not None


def test_superseded_response_is_emitted_with_its_own_response_id():
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="a", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())     # response 1 opens
    _push(obs, LLMTextFrame("<en> first"))
    _push(obs, UserStoppedSpeakingFrame())
    obs.note_user_turn(turn_id=2, user_text="b", stt_eot_received_at=2.0, turn_committed_at=2.0)
    _push(obs, LLMFullResponseStartFrame())     # response 2 supersedes response 1
    assert out and out[-1]["error_stage"] == "superseded_by_new_response"
    assert out[-1]["response_id"] == 1          # the superseded record kept id 1
    assert obs._open["response_id"] == 2        # active record is response 2


def test_end_frame_alone_does_not_finalize_before_parser_note():
    # P1-1 guard: LLMFullResponseEndFrame is timing only; the record must wait
    # for the parser's note_assistant_text even if audio already arrived.
    out: list[dict] = []
    obs = TurnMetricsObserver(on_record=out.append)
    obs.note_user_turn(turn_id=1, user_text="x", stt_eot_received_at=1.0, turn_committed_at=1.0)
    _push(obs, LLMFullResponseStartFrame())
    _push(obs, BotStartedSpeakingFrame())   # audio first
    _push(obs, LLMFullResponseEndFrame())   # end frame alone must NOT finalize
    assert out == []
    obs.note_assistant_text("done")          # parser signal finalizes
    assert len(out) == 1
    assert out[0]["assistant_text"] == "done"
