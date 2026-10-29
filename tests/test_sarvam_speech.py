"""Offline protocol checks; no provider calls or audible assertions."""

import asyncio
import base64
import json
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError
from pipecat.frames.frames import (
    InterimTranscriptionFrame, TranscriptionFrame, UserStoppedSpeakingFrame,
    TTSAudioRawFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame, LLMTextFrame,
)
from pipecat.services.sarvam.tts import SarvamTTSSettings, language_to_sarvam_language

from app.config import get_settings
from app.models.runtime_config_models import RuntimeConfig
from app.runtime import ACTIVE_SESSIONS, build_config_snapshot
from app.services import language, memory
from app.services.call_metrics import TurnMetricsObserver
from app.services.language_prefix import LanguagePrefixProcessor
from app.services.pipecat_pipeline import _build_pipeline_processors, _build_tts
from app.services.sarvam_realtime_stt import SarvamRealtimeSTTService
from app.services.user_input import UserInputGate


def make_stt():
    service = SarvamRealtimeSTTService(api_key="offline-key", settings=SarvamRealtimeSTTService.Settings(model="saaras:v3-realtime"))
    gate = UserInputGate()
    gate.set_muted(False)

    @service.event_handler("on_start_of_turn")
    async def started(_service):
        gate.start_turn()

    service.accept_turn = lambda: gate.accepting_turn
    service.commit_turn = AsyncMock(return_value=True)
    service.push_frame = AsyncMock()
    return service, gate


@pytest.mark.asyncio
async def test_interview_hold_combines_continuation_before_any_commit_and_deduplicates_finals():
    stt, _ = make_stt(); stt.configure_turn_hold(20)
    await stt.handle_message({"event": "vad.speech_start"})
    first = {"event": "transcript.final", "text": "I store the numbers"}
    await stt.handle_message(first); await stt.handle_message(first)
    stt.commit_turn.assert_not_awaited()
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.partial", "text": "and their indices"})
    stt.commit_turn.assert_not_awaited()
    await stt.handle_message({"event": "transcript.final", "text": "and their indices"})
    await stt._release_task
    stt.commit_turn.assert_awaited_once_with(stt, "I store the numbers and their indices")
    finals = [call.args[0] for call in stt.push_frame.await_args_list if isinstance(call.args[0], TranscriptionFrame)]
    assert len(finals) == 1 and finals[0].text == "I store the numbers and their indices"
    assert stt.provider_turn_index == 2
    assert sum(isinstance(call.args[0], UserStoppedSpeakingFrame) for call in stt.push_frame.await_args_list) == 1
    await stt._close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["close", "mute", "failure"])
async def test_delayed_final_never_leaks_after_close_mute_or_provider_failure(cause):
    stt, gate = make_stt(); stt.configure_turn_hold(20)
    stt.push_error_frame = AsyncMock()
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.final", "text": "Pending answer"})
    task = stt._release_task
    if cause == "close": await stt._close()
    elif cause == "failure": await stt._fail("provider stopped")
    else:
        gate.set_muted(True)
        await task
    assert task.done()
    stt.commit_turn.assert_not_awaited()
    assert not any(isinstance(call.args[0], TranscriptionFrame) for call in stt.push_frame.await_args_list)
    await stt._close()


@pytest.mark.asyncio
async def test_new_speech_does_not_cancel_an_in_progress_checkpoint():
    stt, _ = make_stt(); stt.configure_turn_hold(1)
    saving, saved = asyncio.Event(), asyncio.Event()
    async def commit(service, text):
        saving.set(); await saved.wait(); return True
    stt.commit_turn = AsyncMock(side_effect=commit)
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.final", "text": "Already committed"})
    await asyncio.wait_for(saving.wait(), .5)
    continuing = asyncio.create_task(stt.handle_message({"event": "vad.speech_start"}))
    await asyncio.sleep(0)
    assert not continuing.done()
    saved.set(); await continuing
    assert any(isinstance(call.args[0], TranscriptionFrame) for call in stt.push_frame.await_args_list)
    stt.commit_turn.assert_awaited_once()
    await stt._close()


@pytest.mark.parametrize("code", language.SUPPORTED_RESPONSE_LANGUAGES)
@pytest.mark.asyncio
async def test_split_prefix_routes_before_streaming_text(code):
    processor = LanguagePrefixProcessor(call_sid="language-test")
    processor.push_frame = AsyncMock()
    await processor.process_frame(LLMFullResponseStartFrame())
    await processor.process_frame(LLMTextFrame("<" + code[0]))
    await processor.process_frame(LLMTextFrame(code[1] + "> Hello. "))
    frames = [c.args[0] for c in processor.push_frame.await_args_list]
    delta_index = next(i for i, f in enumerate(frames) if isinstance(getattr(f, "delta", None), SarvamTTSSettings))
    text_index = next(i for i, f in enumerate(frames) if getattr(f, "text", "") == "Hello. ")
    assert delta_index < text_index
    assert language_to_sarvam_language(frames[delta_index].delta.language) == code + "-IN"
    assert language.sanitize_language_control_tags(f"<{code}> hello <{code}>") == "hello"


@pytest.mark.parametrize("code", language.SUPPORTED_RESPONSE_LANGUAGES)
@pytest.mark.asyncio
async def test_empty_reply_fallback_matches_selected_language(code):
    from app.services.language_prefix import _SAFE_REPEAT_LINES
    ACTIVE_SESSIONS["fallback-test"] = {"language_state": language.new_language_state()}
    processor = LanguagePrefixProcessor(call_sid="fallback-test")
    processor.push_frame = AsyncMock()
    await processor.process_frame(LLMFullResponseStartFrame())
    await processor.process_frame(LLMTextFrame(f"<{code}>"))
    await processor.process_frame(LLMFullResponseEndFrame())
    text = "".join(getattr(c.args[0], "text", "") for c in processor.push_frame.await_args_list)
    assert text == _SAFE_REPEAT_LINES[code]


@pytest.mark.asyncio
async def test_partial_and_speech_end_do_not_commit_then_final_commits_once():
    stt, _gate = make_stt()
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.partial", "text": "hello", "language": "en-IN"})
    await stt.handle_message({"event": "vad.speech_end"})
    stt.commit_turn.assert_not_awaited()
    assert any(isinstance(c.args[0], InterimTranscriptionFrame) for c in stt.push_frame.await_args_list)
    final = {"event": "transcript.final", "text": "hello", "language": "en-IN"}
    await stt.handle_message(final)
    await stt.handle_message(final)
    stt.commit_turn.assert_awaited_once()
    assert sum(isinstance(c.args[0], TranscriptionFrame) for c in stt.push_frame.await_args_list) == 1
    assert sum(isinstance(c.args[0], UserStoppedSpeakingFrame) for c in stt.push_frame.await_args_list) == 1


@pytest.mark.parametrize("text", ["", " ", "...", "speaker echo"])
@pytest.mark.asyncio
async def test_empty_or_muted_final_never_enters_response_path(text):
    stt, gate = make_stt()
    gate.set_muted(True)
    await stt.handle_message({"event": "vad.speech_start"})
    gate.set_muted(False)  # delayed final from rejected speech remains rejected
    await stt.handle_message({"event": "transcript.final", "text": text})
    stt.commit_turn.assert_not_awaited()
    stt.push_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_rejection_blocks_final_and_turn_release():
    stt, _gate = make_stt()
    stt.commit_turn.return_value = False
    await stt.handle_message({"event": "vad.speech_start"})
    stt.push_frame.reset_mock()
    await stt.handle_message({"event": "transcript.final", "text": "rejected"})
    stt.push_frame.assert_not_awaited()


@pytest.mark.parametrize("code", language.SUPPORTED_RESPONSE_LANGUAGES)
@pytest.mark.asyncio
async def test_final_maps_detected_and_fixed_languages(code):
    stt, _gate = make_stt()
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.final", "text": "example", "language": code + "-IN"})
    final = next(c.args[0] for c in stt.push_frame.await_args_list if isinstance(c.args[0], TranscriptionFrame))
    assert final.language == language.to_language_enum(code) and final.finalized
    stt._config.language_code = code + "-IN"
    await stt.handle_message({"event": "vad.speech_start"})
    await stt.handle_message({"event": "transcript.final", "text": "example"})
    finals = [c.args[0] for c in stt.push_frame.await_args_list if isinstance(c.args[0], TranscriptionFrame)]
    assert len(finals) == 2 and finals[-1].language == language.to_language_enum(code)


@pytest.mark.parametrize("text", ["", "  ", "..."])
@pytest.mark.asyncio
async def test_empty_unmuted_final_does_not_commit(text):
    stt, _gate = make_stt()
    await stt.handle_message({"event": "vad.speech_start"})
    stt.push_frame.reset_mock()
    await stt.handle_message({"event": "transcript.final", "text": text})
    stt.commit_turn.assert_not_awaited()
    stt.push_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_begin_controls_readiness_and_raw_pcm_wire_shape():
    stt, _gate = make_stt()
    waiter = asyncio.create_task(stt.wait_until_ready())
    await asyncio.sleep(0)
    assert not waiter.done()
    stt._socket = MagicMock(send=AsyncMock())
    await stt.handle_message({"event": "session.begin", "request_id": "offline"})
    await asyncio.wait_for(waiter, 0.1)
    pcm = b"\x01\x00" * 160
    _ = [frame async for frame in stt.run_stt(pcm)]
    sent = json.loads(stt._socket.send.call_args.args[0])
    assert sent["event"] == "audio_input"
    assert base64.b64decode(sent["audio"]) == pcm
    assert stt.connected_at is not None


@pytest.mark.asyncio
async def test_errors_preserve_provider_reason_but_redact_credentials():
    stt, _gate = make_stt()
    stt.push_error_frame = AsyncMock()
    await stt.handle_message({"event": "error", "message": "quota exceeded for offline-key", "is_fatal": True})
    frame = stt.push_error_frame.call_args.args[0]
    assert frame.fatal and "offline-key" not in frame.error
    assert "quota exceeded" in frame.error and "[REDACTED]" in frame.error


@pytest.mark.asyncio
async def test_socket_timeout_keeps_cause_and_reports_only_one_failure(monkeypatch):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    from app.services import sarvam_realtime_stt as provider

    stt, _gate = make_stt()
    stt.push_error_frame = AsyncMock()

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def messages(self):
            yield json.dumps({"event": "session.begin"})
            raise ConnectionClosedError(None, Close(1011, "keepalive ping timeout offline-key"))

        def __aiter__(self):
            return self.messages()

    monkeypatch.setattr(provider, "connect", lambda *args, **kwargs: Socket())
    await stt._receive()
    await stt._fail("a second send failure")
    await stt.handle_message({"event": "session.begin"})
    assert not stt._ready.is_set()
    stt.push_error_frame.assert_awaited_once()
    frame = stt.push_error_frame.call_args.args[0]
    assert frame.fatal and "keepalive ping timeout" in frame.error
    assert "1011" in frame.error and "offline-key" not in frame.error


@pytest.mark.asyncio
async def test_real_connection_path_waits_for_session_begin_and_drains_on_cancel(monkeypatch):
    from app.services import sarvam_realtime_stt as provider

    stt, _gate = make_stt()
    socket_open, begin, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    captured = {}

    class Socket:
        async def __aenter__(self):
            socket_open.set()
            return self

        async def __aexit__(self, *args):
            closed.set()

        async def messages(self):
            await begin.wait()
            yield json.dumps({"event": "session.begin"})
            await asyncio.Event().wait()

        def __aiter__(self):
            return self.messages()

    def connect(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Socket()

    async def cancel(task, timeout=None):
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    monkeypatch.setattr(provider, "connect", connect)
    stt.cancel_task = cancel
    stt._connection_task = asyncio.create_task(stt._receive())
    waiter = asyncio.create_task(stt.wait_until_ready())
    await socket_open.wait()
    assert not waiter.done()
    query = parse_qs(urlsplit(captured["url"]).query)
    assert query["sample_rate"] == ["16000"] and query["encoding"] == ["linear16"]
    assert query["mode"] == ["transcribe"] and query["language_code"] == ["auto"]
    assert "offline-key" not in captured["url"]
    assert captured["additional_headers"]["Api-Subscription-Key"] == "offline-key"
    begin.set()
    await asyncio.wait_for(waiter, 0.5)
    await stt._close()
    assert closed.is_set() and stt._connection_task is None and not stt._ready.is_set()


@pytest.mark.asyncio
async def test_builtin_tts_incremental_text_and_pcm_audio():
    tts = _build_tts(build_config_snapshot(get_settings()), get_settings())
    tts._sample_rate = 16000
    from websockets.protocol import State
    tts._websocket = MagicMock(state=State.OPEN, send=AsyncMock())
    tts.start_tts_usage_metrics = AsyncMock()
    _ = [frame async for frame in tts.run_tts("First sentence.", "ctx")]
    sent = json.loads(tts._websocket.send.call_args.args[0])
    assert sent == {"type": "text", "data": {"text": "First sentence."}}
    pcm = b"\x00\x00" * 160

    async def incoming():
        yield json.dumps({"type": "audio", "data": {"audio": base64.b64encode(pcm).decode()}})

    tts._get_websocket = lambda: incoming()
    tts.get_active_audio_context_id = lambda: "ctx"
    tts.stop_ttfb_metrics = AsyncMock()
    tts.append_to_audio_context = AsyncMock()
    await tts._receive_messages()
    frame = tts.append_to_audio_context.call_args.args[1]
    assert isinstance(frame, TTSAudioRawFrame)
    assert (frame.audio, frame.sample_rate, frame.num_channels) == (pcm, 16000, 1)


def test_legacy_config_migrates_without_changing_generation_or_memory():
    config = RuntimeConfig.model_validate({
        "stt": {"model": "flux-general-multi", "eot_timeout_ms": 900, "enable_eager_eot": True},
        "tts": {"provider": "cartesia", "model": "sonic-3", "voice_id": "old-id"},
        "llm": {"temperature": 0.4, "enable_speculative_llm": True},
        "memory": {"recent_turn_pairs": 8},
    })
    assert config.stt.silence_duration_ms == 900
    assert config.tts.voice_id == "shubh"
    assert config.llm.temperature == 0.4 and not config.llm.enable_speculative_llm
    assert config.memory.recent_turn_pairs == 8
    with pytest.raises(ValidationError):
        RuntimeConfig(stt={"enable_eager_eot": True})


@pytest.mark.asyncio
async def test_tts_config_wire_payload_meets_provider_buffer_minimum():
    tts = _build_tts(build_config_snapshot(get_settings()), get_settings())
    tts._speech_sample_rate = "16000"
    tts._websocket = MagicMock(send=AsyncMock())
    await tts._send_config()
    payload = json.loads(tts._websocket.send.call_args.args[0])
    assert payload["type"] == "config"
    assert payload["data"]["min_buffer_size"] == 30
    assert payload["data"]["speech_sample_rate"] == "16000"
    assert payload["data"]["output_audio_codec"] == "linear16"


@pytest.mark.asyncio
async def test_provider_rejection_reason_is_visible_with_credentials_redacted():
    from pipecat.frames.frames import ErrorFrame
    settings = get_settings()
    failures = []
    processors = _build_pipeline_processors(
        transport=MagicMock(), snapshot=build_config_snapshot(settings), settings=settings,
        call_sid="provider-error-test", stream_sid="s",
        metrics_observer=TurnMetricsObserver(on_record=lambda record: None),
        on_provider_error=failures.append,
    )
    await processors[8]._call_event_handler("on_error", ErrorFrame(
        f"Validation Error: min_buffer_size must be >= 30; {settings.sarvam_api_key}",
    ))
    await asyncio.sleep(0)
    assert len(failures) == 1 and "min_buffer_size must be >= 30" in failures[0]
    assert settings.sarvam_api_key not in failures[0] and "[REDACTED]" in failures[0]


@pytest.mark.parametrize("word", ["हिंदी", "मराठी", "ಕನ್ನಡ", "తెలుగు"])
def test_memory_preserves_native_script_marks(word):
    assert word in memory._salient_tokens(word)


@pytest.mark.asyncio
async def test_accepted_final_drives_real_aggregator_and_memory_exactly_once():
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import LLMContextFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair, LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import FrameProcessor
    from app.services.pipecat_pipeline import MemoryProcessor, _build_user_turn_strategies

    response_requested = asyncio.Event()
    requests = []

    class Capture(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                requests.append(frame)
                response_requested.set()
            await self.push_frame(frame, direction)

    stt = SarvamRealtimeSTTService(api_key="offline-key", settings=SarvamRealtimeSTTService.Settings())
    stt.accept_turn = lambda: True
    stt.commit_turn = AsyncMock(return_value=True)

    async def receive():
        await stt.handle_message({"event": "session.begin"})
        await asyncio.Event().wait()

    stt._receive = receive
    context = LLMContext(messages=[{"role": "system", "content": "Be concise."}])
    mem = MemoryProcessor(call_sid="aggregate-test", context=context,
        base_system_prompt="Be concise.", memory_cfg={})
    mem._govern_context = MagicMock(wraps=mem._govern_context)
    aggregators = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(
        user_turn_strategies=_build_user_turn_strategies(turn_stop_timeout_ms=100),
        vad_analyzer=SileroVADAnalyzer(sample_rate=16000),
    ))
    task = PipelineTask(Pipeline([stt, mem, aggregators.user(), Capture(), aggregators.assistant()]),
        params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000))
    running = asyncio.create_task(PipelineRunner(handle_sigint=False).run(task))
    try:
        await asyncio.wait_for(stt.wait_until_ready(), 2)
        await stt.handle_message({"event": "vad.speech_start"})
        await stt.handle_message({"event": "transcript.partial", "text": "hello"})
        await stt.handle_message({"event": "vad.speech_end"})
        final = {"event": "transcript.final", "text": "hello there"}
        await stt.handle_message(final)
        await stt.handle_message(final)
        await asyncio.wait_for(response_requested.wait(), 2)
        await asyncio.sleep(0.25)
        assert len(requests) == 1
        mem._govern_context.assert_called_once_with("hello there")
        stt.commit_turn.assert_awaited_once()
        assert [m["content"] for m in context.messages if m.get("role") == "user"] == ["hello there"]
    finally:
        await task.cancel()
        await asyncio.wait_for(running, 3)
