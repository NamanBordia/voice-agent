"""Slice 8 — Stage-2 metrics: controller marks -> eager_probe, draft_headstart, aggregates."""

import asyncio

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from app.routers.dashboard import _eager_probe_summary
from app.services.call_metrics import TurnMetricsObserver

_BASE_PROBE = {
    "eager_eot_seen": True, "eager_eot_at": 100.0, "eager_transcript": "hello",
    "turn_resumed_seen": False, "turn_resumed_at": None, "resumed_since_eager": False,
    "final_eot_at": 100.3, "final_transcript": "hello",
}


def _emit_probe(probe, marks=None, **overrides):
    out: list[dict] = []
    obs = TurnMetricsObserver(
        on_record=out.append,
        speculation_marks_provider=(lambda: marks) if marks is not None else None,
    )
    rec = {
        "turn_id": 1, "user_text": "hello", "assistant_text": "y",
        "detected_language": "en", "response_language": "en",
        "user_stopped_speaking_at": 100.0, "user_stopped_is_eot_fallback": False,
        "stt_eot_received_at": 100.3, "turn_committed_at": 100.35,
        "llm_response_started_at": 100.5, "llm_first_token_or_text_at": 101.0,
        "llm_completed_at": 101.1, "tts_request_started_at": 101.15,
        "first_audio_frame_sent_at": 101.2, "error_stage": None, "error_message": None,
        "eager_probe": dict(probe),
    }
    rec.update(overrides)
    obs._emit(rec)
    return out[0]["eager_probe"]


def test_probe_records_draft_started_and_released():
    ep = _emit_probe(_BASE_PROBE, marks={
        "draft_started": True, "draft_hit": True, "draft_released": True,
        "draft_first_token_at": 100.0,
    })
    assert ep["draft_started"] is True
    assert ep["draft_released"] is True
    assert ep["draft_used"] is True
    assert ep["normal_llm_used"] is False
    assert ep["extra_llm_call"] == 0           # released -> replaces the normal call
    assert ep["draft_headstart_ms"] == 300.0   # (100.3 - 100.0) * 1000
    # Stage-1 fields still computed alongside
    assert ep["verdict"] == "eligible"
    assert ep["eager_final_match"] is True


def test_probe_records_draft_cancelled_and_normal_used():
    probe = {**_BASE_PROBE, "turn_resumed_seen": True, "resumed_since_eager": True}
    ep = _emit_probe(probe, marks={
        "draft_started": True, "draft_cancelled": True,
        "draft_cancel_reason": "turn_resumed", "turn_resumed_after_draft": True,
    })
    assert ep["draft_cancelled"] is True
    assert ep["draft_cancel_reason"] == "turn_resumed"
    assert ep["normal_llm_used"] is True
    assert ep["draft_used"] is False
    assert ep["extra_llm_call"] == 1           # wasted draft + normal call
    assert ep["draft_headstart_ms"] is None
    # Stage-1 verdict unchanged for a resumed turn
    assert ep["verdict"] == "wasted_turn_resumed"
    assert ep["estimated_saved_ms"] == 0.0


def test_draft_headstart_clamps_to_zero_when_token_after_eot():
    ep = _emit_probe(_BASE_PROBE, marks={
        "draft_started": True, "draft_released": True, "draft_first_token_at": 100.5,
    })  # first token AFTER final_eot_at (100.3) -> clamp
    assert ep["draft_headstart_ms"] == 0.0


def test_no_provider_adds_no_draft_keys():
    ep = _emit_probe(_BASE_PROBE, marks=None)  # speculation disabled
    assert "draft_started" not in ep
    assert "draft_headstart_ms" not in ep
    assert "normal_llm_used" not in ep
    assert "extra_llm_call" not in ep
    # Stage-1 fields intact
    assert ep["verdict"] == "eligible"


def test_no_draft_this_turn_keeps_normal_used_without_extra_call():
    # provider present (speculation on) but this turn started no draft (no eager fired)
    ep = _emit_probe(_BASE_PROBE, marks={})
    assert ep["normal_llm_used"] is True
    assert ep["draft_used"] is False
    assert ep["extra_llm_call"] == 0
    assert ep["draft_headstart_ms"] is None


def test_marks_snapshotted_at_promote_survive_next_turn_reset():
    # REGRESSION (review MEDIUM): the marks are pinned at the record's own Start frame
    # (promote), so a next-turn on_turn_start that wipes the live marks BEFORE finalize
    # can't mislabel a won draft as normal_llm_used.
    out: list[dict] = []
    live = {"draft_started": True, "draft_released": True, "draft_first_token_at": 100.0}
    obs = TurnMetricsObserver(on_record=out.append, speculation_marks_provider=lambda: live)

    def push(frame):
        asyncio.run(obs.on_push_frame(FramePushed(
            source=None, destination=None, frame=frame,
            direction=FrameDirection.DOWNSTREAM, timestamp=0,
        )))

    push(UserStoppedSpeakingFrame())
    obs.note_user_turn(
        turn_id=1, user_text="hello", stt_eot_received_at=100.3, turn_committed_at=100.35,
        eager_probe={"eager_eot_seen": True, "eager_eot_at": 100.0, "eager_transcript": "hello",
                     "turn_resumed_seen": False, "resumed_since_eager": False},
    )
    push(LLMFullResponseStartFrame())   # promote -> snapshot captures turn-1 live marks
    live.clear()                         # simulate next turn's on_turn_start wiping marks
    push(LLMTextFrame("<en> Hi"))
    push(LLMFullResponseEndFrame())
    obs.note_assistant_text("Hi")
    push(BotStartedSpeakingFrame())      # -> finalize

    assert len(out) == 1
    ep = out[0]["eager_probe"]
    assert ep["draft_released"] is True              # from the promote-time snapshot, not the cleared live dict
    assert ep["draft_used"] is True
    assert ep["normal_llm_used"] is False
    assert ep["draft_headstart_ms"] == 300.0         # (100.3 - 100.0) * 1000
    assert "_spec_marks_snapshot" not in out[0]       # internal key stripped


def test_dashboard_rollup_aggregates_draft_outcomes():
    records = [
        {  # released (won)
            "user_stop_to_first_audio_ms": 700,
            "eager_probe": {"eager_eot_seen": True, "verdict": "eligible",
                            "eager_final_match": True, "head_start_ms": 30,
                            "draft_started": True, "draft_released": True,
                            "draft_used": True, "normal_llm_used": False,
                            "extra_llm_call": 0, "draft_headstart_ms": 250},
        },
        {  # cancelled (resumed) -> normal LLM
            "user_stop_to_first_audio_ms": 1000,
            "eager_probe": {"eager_eot_seen": True, "verdict": "wasted_turn_resumed",
                            "turn_resumed_seen": True, "draft_started": True,
                            "draft_cancelled": True, "normal_llm_used": True,
                            "extra_llm_call": 1, "draft_headstart_ms": None},
        },
        {  # no eager / no draft -> normal LLM
            "user_stop_to_first_audio_ms": 950,
            "eager_probe": {"eager_eot_seen": False, "normal_llm_used": True,
                            "extra_llm_call": 0},
        },
    ]
    s = _eager_probe_summary(records)
    assert s["speculation_on"] is True
    assert s["draft_start_count"] == 2
    assert s["draft_release_count"] == 1
    assert s["draft_cancel_count"] == 1
    assert s["normal_llm_used_count"] == 2
    assert s["draft_use_rate"] == 0.5                 # 1 of 2 started
    assert s["cancel_rate"] == 0.5
    assert s["additional_llm_call_rate"] == round(1 / 3, 3)
    assert s["median_draft_headstart_ms"] == 250
    # empirical end-to-end: median(normal first-audio) - median(released)
    assert s["median_user_stop_to_first_audio_released_ms"] == 700
    assert s["median_user_stop_to_first_audio_normal_ms"] == 975  # median(1000, 950)
    assert s["empirical_saved_ms_population"] == 275


def test_dashboard_rollup_omits_stage2_keys_when_no_draft_started():
    # eager-only call (Stage-1 telemetry, speculation off): no draft_* aggregate keys.
    records = [
        {"user_stop_to_first_audio_ms": 800,
         "eager_probe": {"eager_eot_seen": True, "verdict": "eligible",
                         "eager_final_match": True, "head_start_ms": 40,
                         "estimated_saved_ms": 290}},
    ]
    s = _eager_probe_summary(records)
    assert "speculation_on" not in s
    assert "draft_start_count" not in s
    # Stage-1 rollup still present
    assert s["eager_eot_turns"] == 1
