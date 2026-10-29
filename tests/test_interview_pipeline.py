"""Exercise actual aggregator/frame ordering with mocked provider decisions."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import BotStoppedSpeakingFrame, LLMContextFrame, LLMTextFrame, TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from app.config import get_settings
from app.interviews.models import Decision
from app.interviews.processor import InterviewResponseProcessor
from app.interviews.service import InterviewBinding
from app.models.browser_models import OfferRequest
from app.runtime import ACTIVE_SESSIONS, build_config_snapshot
from app.services.pipecat_pipeline import MemoryProcessor, _build_pipeline_processors, _build_user_turn_strategies
from app.services.call_metrics import TurnMetricsObserver
from app.services.sarvam_realtime_stt import SarvamRealtimeSTTService
from test_browser_session import setup_manager, spin_until
from test_interview_service import activated


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_prompt", [False, True])
@pytest.mark.parametrize("continued", [False, True])
async def test_real_aggregator_generates_once_and_emits_only_validated_spoken_text(tmp_path, missing_prompt, continued):
    decisions = AsyncMock(return_value=Decision(action="follow_up", question_id="q_1",
        turn_kind="answer", spoken_text="" if missing_prompt else "Where did you check the JWT?",
        followup_excerpt="" if missing_prompt else "JWT"))
    svc, definition, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    await binding.start("call1", build_config_snapshot(get_settings())); await binding.delivered()
    generated = asyncio.Event(); output = []
    class Capture(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMTextFrame): output.append(frame.text); generated.set()
            await self.push_frame(frame, direction)
    stt = SarvamRealtimeSTTService(api_key="offline", settings=SarvamRealtimeSTTService.Settings())
    if continued:
        stt.configure_turn_hold(20)
    stt.accept_turn = lambda: True
    async def commit(service, text): return await binding.accept(f"call1:{service.provider_turn_index}", text)
    stt.commit_turn = commit
    async def receive():
        await stt.handle_message({"event": "session.begin"}); await asyncio.Event().wait()
    stt._receive = receive
    context = LLMContext(messages=[{"role":"system", "content":"Interview"}])
    memory = MemoryProcessor(call_sid="call1", context=context, base_system_prompt="Interview", memory_cfg={})
    memory._govern_context = MagicMock(wraps=memory._govern_context)
    aggregators = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        user_turn_strategies=_build_user_turn_strategies(turn_stop_timeout_ms=100),
        vad_analyzer=SileroVADAnalyzer(sample_rate=16000)))
    response = InterviewResponseProcessor(binding)
    task = PipelineTask(Pipeline([stt, memory, aggregators.user(), response, Capture(), aggregators.assistant()]),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000))
    running = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await asyncio.wait_for(stt.wait_until_ready(), 3)
        await stt.handle_message({"event":"vad.speech_start"})
        final = {"event":"transcript.final", "text":"I used JWT"}
        await stt.handle_message(final); await stt.handle_message(final)
        if continued:
            await stt.handle_message({"event": "vad.speech_start"})
            await stt.handle_message({"event": "transcript.final", "text": "for login"})
        await asyncio.wait_for(generated.wait(), 3)
        # Duplicate aggregator contexts are ignored after the decision checkpoint.
        await response.process_frame(LLMContextFrame(context=context), FrameDirection.DOWNSTREAM)
        await asyncio.sleep(.1)
        expected = definition.plan.sections[0].questions[0].text if missing_prompt else "Where did you check the JWT?"
        assert output == [f"<en>{expected}"]
        assert decisions.await_count == 1
        governed_context = decisions.call_args.args[3]
        assert "OUTPUT PROTOCOL" in governed_context[0]["content"]
        accepted = "I used JWT for login" if continued else "I used JWT"
        assert [message["content"] for message in governed_context if message["role"] == "user"] == [accepted]
        memory._govern_context.assert_called_once_with(accepted)
        assert len((await svc.session(state.id)).answers) == 1
        assert (await svc.session(state.id)).response_in_progress
        await response.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert not (await svc.session(state.id)).response_in_progress
    finally:
        await task.cancel(); await asyncio.wait_for(running, 3); await binding.stop()


@pytest.mark.asyncio
async def test_interview_assembly_has_one_generator_in_original_slot(tmp_path):
    svc, definition, state, _ = await activated(tmp_path)
    binding = InterviewBinding(svc, state.id)
    processors = _build_pipeline_processors(transport=MagicMock(), snapshot=build_config_snapshot(get_settings()),
        settings=get_settings(), call_sid="c", stream_sid="s", metrics_observer=TurnMetricsObserver(on_record=lambda _: None), interview=binding)
    assert len(processors) == 11
    assert isinstance(processors[6], InterviewResponseProcessor)
    assert next(p for p in processors if isinstance(p, SarvamRealtimeSTTService))._hold_seconds == .5
    assert not any(p.__class__.__name__ == "VoiceCerebrasLLMService" for p in processors)


@pytest.mark.asyncio
async def test_manager_keeps_single_runner_greeting_cleanup_and_resume_checkpoint(setup_manager, tmp_path):
    manager, peer, task, ready, runs = setup_manager
    svc, definition, state, _ = await activated(tmp_path)
    binding = InterviewBinding(svc, state.id)
    with patch("app.services.browser_session.finalize_call_log", wraps=__import__('app.utils.metrics_logger', fromlist=['finalize_call_log']).finalize_call_log) as finalized:
        await manager.offer(OfferRequest(sdp="offer", type="offer"), interview=binding)
        call_sid = manager._live.call_sid
        await task.rtvi.emit("on_client_ready")
        await task.rtvi.emit("on_client_message", SimpleNamespace(type="ui-ready"))
        assert task.frames == []
        ready.set()
        # Checkpoint I/O runs in threads; wait for it without polling the filesystem.
        for _ in range(100):
            if any(isinstance(f, TTSSpeakFrame) for f in task.frames): break
            await asyncio.sleep(.01)
        assert any(isinstance(f, TTSSpeakFrame) and definition.plan.sections[0].questions[0].text in f.text for f in task.frames)
        with pytest.raises(Exception): await manager.offer(OfferRequest(sdp="offer", type="offer"))
        assert len(runs) == 1 and runs[0]["hooks"].interview is binding
        first, second = await asyncio.gather(manager.end(call_sid), manager.end(call_sid))
        assert first.saved and second.saved and finalized.call_count == 1
        checkpoint = await svc.session(state.id)
        assert checkpoint.status == "paused" and checkpoint.current_call_sid is None
        assert peer.closed and ACTIVE_SESSIONS == {}
        assert manager._live is None


@pytest.mark.asyncio
async def test_invalid_decision_emits_only_reviewed_fallback_and_does_not_pause(tmp_path):
    decisions = AsyncMock(return_value=Decision(action="next_question", question_id="unknown", turn_kind="answer"))
    svc, definition, state, _ = await activated(tmp_path, decisions)
    binding = InterviewBinding(svc, state.id)
    await binding.start("c", build_config_snapshot(get_settings())); await binding.delivered(); await binding.accept("1", "answer")
    processor = InterviewResponseProcessor(binding)
    processor.push_frame = AsyncMock(); processor.push_error = AsyncMock()
    with patch.object(FrameProcessor, "process_frame", new=AsyncMock()):
        await processor.process_frame(LLMContextFrame(context=LLMContext()), FrameDirection.DOWNSTREAM)
    processor.push_error.assert_not_awaited()
    assert [call.args[0].text for call in processor.push_frame.call_args_list
        if isinstance(call.args[0], LLMTextFrame)] == [f"<en>{definition.plan.sections[0].questions[0].text}"]
    assert (await svc.session(state.id)).pending_event_id is None
    assert (await svc.session(state.id)).status == "in_progress"
    assert (await svc.session(state.id)).response_in_progress
    await processor.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
    assert not (await svc.session(state.id)).response_in_progress
    await binding.stop()
    assert (await svc.session(state.id)).status == "paused"


@pytest.mark.asyncio
async def test_retry_save_also_retries_interview_checkpoint_without_refinalizing(setup_manager, tmp_path):
    manager, _, _, _, _ = setup_manager
    svc, _, state, _ = await activated(tmp_path)
    binding = InterviewBinding(svc, state.id)
    original_stop = binding.stop
    attempts = 0
    async def interrupted_stop(error=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("disk temporarily full")
        await original_stop(error)
    binding.stop = interrupted_stop
    with patch("app.services.browser_session.finalize_call_log", wraps=__import__('app.utils.metrics_logger', fromlist=['finalize_call_log']).finalize_call_log) as finalized:
        await manager.offer(OfferRequest(sdp="offer", type="offer"), interview=binding)
        call_sid = manager._live.call_sid
        assert not (await manager.end(call_sid)).saved
        assert (await svc.session(state.id)).status == "in_progress"
        retried = await manager.end(call_sid)
        assert retried.saved and retried.error is None
        assert (await svc.session(state.id)).status == "paused"
        assert finalized.call_count == 1 and attempts == 2
