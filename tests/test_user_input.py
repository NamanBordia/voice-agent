"""Mute boundary prevents speaker echo from reaching memory and speculative replies."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from app.config import get_settings
from app.runtime import ACTIVE_SESSIONS, build_config_snapshot
from app.services.call_metrics import TurnMetricsObserver
from app.services.pipecat_pipeline import _build_pipeline_processors
from app.services.user_input import UserInputGate


@pytest.mark.asyncio
async def test_muted_audio_is_silence_at_the_same_rate_and_unmuted_audio_is_preserved():
    gate = UserInputGate()
    gate.push_frame = AsyncMock()
    frame = InputAudioRawFrame(audio=b"\x11\x22" * 160, sample_rate=16000, num_channels=1)
    await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
    sent = gate.push_frame.call_args.args[0]
    assert sent.audio == bytes(320) and sent.sample_rate == 16000 and sent.num_channels == 1
    gate.set_muted(False)
    await gate.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert gate.push_frame.call_args.args[0] is frame


@pytest.mark.asyncio
async def test_rejected_turn_does_not_update_language_memory_history_or_eager_draft():
    snapshot = build_config_snapshot(get_settings())
    snapshot["runtime_config"]["stt"].update(enable_eager_eot=True, eager_eot_threshold=0.5)
    snapshot["runtime_config"]["llm"]["enable_speculative_llm"] = True
    procs = _build_pipeline_processors(transport=MagicMock(), snapshot=snapshot,
        settings=get_settings(), call_sid="muted-test", stream_sid="s", metrics_observer=TurnMetricsObserver(on_record=lambda r: None))
    gate, stt, hints, user = procs[1], procs[2], procs[3], procs[5]
    hints.push_frame = AsyncMock()
    await stt._call_event_handler("on_start_of_turn")
    await asyncio.sleep(0)
    await stt._call_event_handler("on_eager_end_of_turn", "speaker echo")
    await stt.commit_turn(stt, "speaker echo")
    frame = TranscriptionFrame("speaker echo", "u", "now", finalized=True)
    await hints.process_frame(frame)
    hints.push_frame.assert_not_awaited()
    assert len(procs) == 11
    assert "muted-test" not in ACTIVE_SESSIONS
    await user._call_event_handler("on_user_mute_stopped")
    await asyncio.sleep(0)
    assert not gate.accepting_turn  # A delayed fragment from the muted turn is still rejected.
    await stt._call_event_handler("on_start_of_turn")
    await asyncio.sleep(0)
    assert gate.accepting_turn
    await hints.process_frame(TranscriptionFrame("hello", "u", "now", finalized=True))
    assert hints.push_frame.await_count == 1
