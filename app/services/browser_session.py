"""Single-conversation ownership for browser audio and once-only teardown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import time
from typing import Any, cast
from uuid import uuid4

from aiortc.sdp import candidate_from_sdp
from fastapi import HTTPException
from loguru import logger
from pipecat.frames.frames import TTSSpeakFrame, TTSUpdateSettingsFrame
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.pipeline.task import PipelineTask
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pydantic import ValidationError

from app.config import Settings
from app.models.browser_models import AudioDiagnostics, CandidateRequest, EndResult, OfferRequest
from app.models.schemas import CallLog
from app.runtime import ConfigSnapshot, create_session, delete_session, get_session, update_session_metric
from app.services.language import (
    sanitize_language_control_tags, to_language_enum, try_parse_language_prefix,
)
from app.services.pipecat_pipeline import PipelineExecutionResult, PipelineHooks, _record_greeting, run_pipeline
from app.services.runtime_config import build_effective_snapshot, get_cached_runtime_config
from app.services.transport_factory import build_browser_transport
from app import storage
from app.utils.metrics_logger import finalize_call_log


@dataclass
class _LiveSession:
    connection: SmallWebRTCConnection
    call_sid: str
    stream_sid: str
    snapshot: ConfigSnapshot
    deadline: float
    task: PipelineTask | None = None
    runner: asyncio.Task[None] | None = None
    setup: asyncio.Task[dict[str, str]] | None = None
    greeting: asyncio.Task[None] | None = None
    watchdog: asyncio.Task[None] | None = None
    cleanup: asyncio.Task[EndResult] | None = None
    attached: asyncio.Event = field(default_factory=asyncio.Event)
    media_ready: asyncio.Event = field(default_factory=asyncio.Event)
    rtvi_ready: asyncio.Event = field(default_factory=asyncio.Event)
    ui_ready: asyncio.Event = field(default_factory=asyncio.Event)
    signaling: asyncio.Lock = field(default_factory=asyncio.Lock)
    result: PipelineExecutionResult | None = None
    error: str | None = None
    disconnected: bool = False
    stopping: bool = False
    greeting_queued: bool = False


class BrowserSessionManager:
    """Own live objects; conversation data remains exclusively in app.runtime."""

    def __init__(
        self,
        settings: Settings,
        *,
        connection_factory: Callable[[], SmallWebRTCConnection] | None = None,
        pipeline: Callable[..., Awaitable[PipelineExecutionResult]] = run_pipeline,
    ) -> None:
        self._settings = settings
        self._connection_factory = connection_factory or (lambda: SmallWebRTCConnection(ice_servers=[]))
        self._pipeline = pipeline
        self._lock = asyncio.Lock()
        self._live: _LiveSession | None = None
        self._last_result: EndResult | None = None
        self._pending_logs: dict[str, CallLog] = {}
        self._save_lock = asyncio.Lock()
        self._save_tasks: dict[str, asyncio.Task[EndResult]] = {}
        self._closing = False
        self._stuck = False

    async def offer(self, request: OfferRequest) -> dict[str, str]:
        """Reserve before initialization; retain every task that can outlive HTTP."""
        async with self._lock:
            if self._closing or self._stuck:
                raise HTTPException(503, "Server is stopping; restart if cleanup failed")
            live = self._live
            if request.pc_id:
                if live is None or request.pc_id != live.connection.pc_id:
                    raise HTTPException(404, "Unknown peer connection")
                if live.stopping:
                    raise HTTPException(409, "Conversation is stopping")
                existing = True
            elif live is not None:
                raise HTTPException(409, "A conversation is already active or stopping")
            else:
                config = get_cached_runtime_config()
                if config is None:
                    raise HTTPException(503, "Settings have not been loaded")
                if not config.tts.voice_id.strip():
                    raise HTTPException(409, "Choose a voice in Settings")
                call_sid, stream_sid = f"browser_{uuid4().hex}", f"browser_stream_{uuid4().hex}"
                snapshot = cast(ConfigSnapshot, build_effective_snapshot(config))
                live = _LiveSession(self._connection_factory(), call_sid, stream_sid, snapshot,
                                    time.monotonic() + config.ops.browser_ready_timeout_secs)
                self._live = live
                create_session(call_sid, stream_sid, self._settings, config_snapshot=snapshot)
                self._register_connection(live)
                live.watchdog = asyncio.create_task(self._watch_readiness(live))
                live.setup = asyncio.create_task(self._initialize(live, request))
                existing = False

        try:
            if existing:
                if request.restart_pc:
                    raise HTTPException(409, "Start a new conversation after a connection restart")
                async with live.signaling:
                    await live.connection.renegotiate(request.sdp, request.type, restart_pc=False)
                    answer = live.connection.get_answer()
                    if answer is None:
                        raise RuntimeError("No WebRTC answer")
                    return answer
            assert live.setup is not None
            return await asyncio.shield(live.setup)
        except HTTPException:
            raise
        except asyncio.CancelledError:
            self._request_stop(live, disconnected=True)
            raise
        except Exception:
            logger.exception("Browser offer failed", call_sid=live.call_sid)
            await asyncio.shield(self._request_stop(live, error="Browser connection setup failed"))
            raise HTTPException(500, "Browser connection setup failed") from None

    async def candidates(self, request: CandidateRequest) -> None:
        """Apply candidates only to the current connection."""
        live = self._live
        if live is None or live.stopping or request.pc_id != live.connection.pc_id:
            raise HTTPException(404, "Unknown peer connection")
        try:
            async with live.signaling:
                for item in request.candidates:
                    candidate = candidate_from_sdp(item.candidate.removeprefix("candidate:"))
                    candidate.sdpMid = item.sdp_mid
                    candidate.sdpMLineIndex = item.sdp_mline_index
                    await live.connection.add_ice_candidate(candidate)
        except (ValueError, AssertionError, IndexError):
            raise HTTPException(422, "Invalid ICE candidate") from None

    async def end(self, call_sid: str) -> EndResult:
        """Join retained cleanup or retry a finalized log without finalizing twice."""
        if call_sid in self._pending_logs:
            return await self._save_finalized(call_sid)
        if self._last_result is not None and self._last_result.call_sid == call_sid:
            return self._last_result
        live = self._live
        if live is None or live.call_sid != call_sid:
            raise HTTPException(404, "Unknown conversation")
        cleanup = self._request_stop(live, disconnected=True)
        budget = live.snapshot["runtime_config"]["ops"]["shutdown_timeout_secs"]
        try:
            return await asyncio.wait_for(asyncio.shield(cleanup), timeout=budget)
        except TimeoutError:
            logger.warning("End wait expired; retained cleanup continues", call_sid=call_sid)
            return EndResult(call_sid=call_sid, saved=False,
                             error="Conversation is still ending. Retry saving shortly.")

    async def close(self) -> None:
        """Reject new starts and drain the active conversation on app shutdown."""
        self._closing = True
        if self._live is not None:
            await asyncio.shield(self._request_stop(self._live, error="Application stopped"))
        for call_sid in list(self._pending_logs):
            await self._save_finalized(call_sid)

    def _register_connection(self, live: _LiveSession) -> None:
        @live.connection.event_handler("closed")
        async def closed(_connection: Any) -> None:
            self._request_stop(live, disconnected=True)

    async def _initialize(self, live: _LiveSession, request: OfferRequest) -> dict[str, str]:
        async with live.signaling:
            await live.connection.initialize(request.sdp, request.type)
            answer = live.connection.get_answer()
            if answer is None:
                raise RuntimeError("No WebRTC answer")
            live.runner = asyncio.create_task(self._run(live))
            await live.attached.wait()
            if live.stopping:
                raise RuntimeError("Pipeline initialization failed")
            return answer

    async def _run(self, live: _LiveSession) -> None:
        def disconnected() -> None:
            self._request_stop(live, disconnected=True)

        def provider_error(error: str) -> None:
            self._request_stop(live, error=error)

        try:
            hooks = PipelineHooks(
                on_task=lambda task, ready: self._attach(live, task, ready),
                on_connected=live.media_ready.set,
                on_disconnected=disconnected,
                on_error=provider_error,
            )
            live.result = await self._pipeline(
                transport=build_browser_transport(live.connection), call_sid=live.call_sid,
                stream_sid=live.stream_sid, snapshot=live.snapshot, settings=self._settings, hooks=hooks,
            )
            if live.result.error:
                live.error = live.error or live.result.error
            elif not live.result.completed:
                live.error = live.error or "Pipeline execution did not complete"
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Browser pipeline failed", call_sid=live.call_sid)
            live.error = live.error or "Pipeline execution failed"
        finally:
            live.attached.set()  # Release setup if assembly failed before handoff.
            self._request_stop(live)

    def _attach(self, live: _LiveSession, task: PipelineTask, stt_ready: Callable[[], Awaitable[None]]) -> None:
        live.task = task

        @task.rtvi.event_handler("on_client_ready")
        async def client_ready(rtvi: Any) -> None:
            live.rtvi_ready.set()
            await rtvi.send_server_message({"type": "session", "call_sid": live.call_sid})

        @task.rtvi.event_handler("on_client_message")
        async def client_message(_rtvi: Any, message: Any) -> None:
            if message.type == "ui-ready":
                live.ui_ready.set()
            elif message.type == "audio-diagnostics":
                try:
                    data = AudioDiagnostics.model_validate(message.data)
                except ValidationError:
                    logger.warning("Invalid microphone diagnostics ignored", call_sid=live.call_sid)
                else:
                    logger.info("Browser microphone settings", call_sid=live.call_sid,
                                capture=data.model_dump(exclude_none=True))

        live.greeting = asyncio.create_task(self._greet(live, stt_ready))
        live.attached.set()

    async def _greet(self, live: _LiveSession, stt_ready: Callable[[], Awaitable[None]]) -> None:
        try:
            await live.media_ready.wait()
            await live.rtvi_ready.wait()
            await live.ui_ready.wait()
            await stt_ready()
            if live.stopping:
                return
            assert live.task is not None
            configured_text = live.snapshot["GREETING_TEXT"]
            greeting_language, _, _ = try_parse_language_prefix(configured_text)
            text = sanitize_language_control_tags(configured_text)
            if not text:
                raise RuntimeError("Opening greeting is empty")
            live.greeting_queued = True
            _record_greeting(call_sid=live.call_sid, greeting_text=text)
            update_session_metric(live.call_sid, "greeting_text_source", "configured_text")
            update_session_metric(live.call_sid, "first_tts_frame_queued_at", time.time())
            if greeting_language is not None:
                await live.task.queue_frame(TTSUpdateSettingsFrame(
                    delta=SarvamTTSService.Settings(language=to_language_enum(greeting_language))
                ))
            await live.task.queue_frame(TTSSpeakFrame(text, append_to_context=True))
        except asyncio.CancelledError:
            raise
        except Exception:
            self._request_stop(live, error="Agent readiness or greeting failed")

    async def _watch_readiness(self, live: _LiveSession) -> None:
        await asyncio.sleep(max(0, live.deadline - time.monotonic()))
        if not live.greeting_queued:
            self._request_stop(live, error="Browser or speech provider readiness timed out")

    def _request_stop(self, live: _LiveSession, *, error: str | None = None,
                      disconnected: bool = False) -> asyncio.Task[EndResult]:
        if not live.stopping:
            live.error = live.error or error
            live.disconnected = disconnected and live.error is None
        elif error and live.error is None:
            live.error, live.disconnected = error, False
        if live.cleanup is None:
            live.stopping = True
            live.cleanup = asyncio.create_task(self._cleanup(live))
        return live.cleanup

    async def _cleanup(self, live: _LiveSession) -> EndResult:
        started = time.monotonic()
        # Let an already failed runner's setup caller report its own HTTP error.
        setup = live.setup
        if live.attached.is_set() and live.runner is not None and live.runner.done():
            setup = None
        pending = [t for t in (setup, live.greeting, live.watchdog) if t is not None and not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            # HTTP callers may stop waiting, but ownership of these tasks is retained.
            await asyncio.gather(*pending, return_exceptions=True)
        logger.info("Browser setup drained", call_sid=live.call_sid,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1))
        budget = live.snapshot["runtime_config"]["ops"]["shutdown_timeout_secs"]
        try:
            if live.task is not None and live.runner is not None and not live.runner.done():
                await live.task.cancel()
            if live.runner is not None:
                await asyncio.wait_for(asyncio.shield(live.runner), timeout=budget)
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError) and asyncio.current_task().cancelling():
                raise  # Process shutdown must not finalize a still-running pipeline.
            if live.runner is not None and not live.runner.done():
                logger.warning("Pipeline is slow to stop; waiting for cancellation cleanup",
                               call_sid=live.call_sid)
                live.runner.cancel()
                # Never abandon the finalizer just because a deadline elapsed.
                await asyncio.gather(live.runner, return_exceptions=True)
            if not isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                live.error = live.error or "Pipeline cancellation failed"
                live.disconnected = False
        logger.info("Browser runner drained", call_sid=live.call_sid,
                    elapsed_ms=round((time.monotonic() - started) * 1000, 1))

        try:
            session = get_session(live.call_sid)
            if session is not None:
                result = live.result
                log = finalize_call_log(
                    call_sid=live.call_sid, stream_sid=live.stream_sid,
                    config_snapshot=session["config_snapshot"], metrics=session["metrics"],
                    transcript=session["transcript"], completed=live.error is None and (result is None or result.completed),
                    error=live.error, disconnected=live.disconnected and live.error is None,
                )
                self._pending_logs[live.call_sid] = log
        except Exception:
            logger.exception("Conversation finalization failed", call_sid=live.call_sid)
        finally:
            try:
                await asyncio.wait_for(live.connection.disconnect(), timeout=2)
            except Exception:
                logger.warning("Peer cleanup failed", call_sid=live.call_sid)
                self._stuck = True
            if self._live is live:
                self._live = None
        if live.call_sid not in self._pending_logs:
            return EndResult(call_sid=live.call_sid, saved=False,
                             error="Conversation could not be finalized; check server logs")
        return await self._save_finalized(live.call_sid)

    async def _save_finalized(self, call_sid: str) -> EndResult:
        """Keep a write transaction alive if its HTTP caller is canceled."""
        task = self._save_tasks.get(call_sid)
        if task is None:
            task = asyncio.create_task(self._write_finalized(call_sid))
            self._save_tasks[call_sid] = task
            task.add_done_callback(lambda _task: self._save_tasks.pop(call_sid, None))
        return await asyncio.shield(task)

    async def _write_finalized(self, call_sid: str) -> EndResult:
        """Serialize write retries and retain failed payloads in memory until saved."""
        async with self._save_lock:
            log = self._pending_logs.get(call_sid)
            if log is None:
                if self._last_result is not None and self._last_result.call_sid == call_sid:
                    return self._last_result
                raise HTTPException(404, "Unknown conversation")
            try:
                await storage.persist_call_log(log)
            except Exception:
                logger.exception("Conversation save failed; payload retained for retry", call_sid=call_sid)
                return EndResult(call_sid=call_sid, saved=False,
                                 error="Conversation could not be saved. Retry saving before closing the server.")
            del self._pending_logs[call_sid]
            delete_session(call_sid)
            self._last_result = EndResult(call_sid=call_sid, saved=True,
                                          history_url=f"/calls/{call_sid}", error=log.error)
            logger.info("Conversation saved", call_sid=call_sid)
            return self._last_result
