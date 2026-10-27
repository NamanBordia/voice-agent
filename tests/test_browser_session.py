"""Browser lifecycle verification without microphones, providers, or voice claims."""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
import pytest

from app import storage
from app.config import get_settings
from app.models.browser_models import CandidateRequest, OfferRequest
from app.runtime import ACTIVE_SESSIONS
from app.services.browser_session import BrowserSessionManager
from app.services.pipecat_pipeline import PipelineExecutionResult
from app.services import runtime_config
from app.utils.metrics_logger import finalize_call_log


class Events:
    def __init__(self):
        self.handlers = {}
        self.messages = []

    def event_handler(self, name):
        def register(callback):
            self.handlers.setdefault(name, []).append(callback)
            return callback
        return register

    async def emit(self, name, *args):
        for callback in self.handlers.get(name, []):
            await callback(self, *args)

    async def send_server_message(self, message):
        self.messages.append(message)


class Peer(Events):
    def __init__(self):
        super().__init__()
        self.pc_id = uuid4().hex
        self.closed = False
        self.initialize_gate = None

    async def initialize(self, sdp, type):
        if self.initialize_gate:
            await self.initialize_gate.wait()
        if sdp == "bad":
            raise ValueError("Invalid SDP")

    def get_answer(self):
        return {"sdp": "answer", "type": "answer", "pc_id": self.pc_id}

    async def renegotiate(self, sdp, type, restart_pc=False):
        pass

    async def add_ice_candidate(self, candidate):
        pass

    async def disconnect(self):
        self.closed = True
        await self.emit("closed")


class Task:
    def __init__(self):
        self.rtvi = Events()
        self.frames = []
        self.finished = asyncio.Event()

    async def queue_frame(self, frame):
        self.frames.append(frame)

    async def cancel(self):
        self.finished.set()


@pytest.fixture
def setup_manager(monkeypatch):
    config = runtime_config._store.get()
    config.tts.voice_id = "shubh"
    runtime_config._store._config = config
    peer, task, ready = Peer(), Task(), asyncio.Event()
    runs = []

    async def pipeline(**kwargs):
        runs.append(kwargs)
        kwargs["hooks"].on_task(task, ready.wait)
        kwargs["hooks"].on_connected()
        await task.finished.wait()
        # Normal cancellation can return normally; no CancelledError is required.
        return PipelineExecutionResult(completed=True)

    monkeypatch.setattr("app.services.browser_session.build_browser_transport", lambda c: c)
    manager = BrowserSessionManager(get_settings(), connection_factory=lambda: peer, pipeline=pipeline)
    return manager, peer, task, ready, runs


async def spin_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0)
    assert predicate()


@pytest.mark.asyncio
async def test_greeting_requires_all_readiness_and_only_queues_once(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    await task.rtvi.emit("on_client_ready")
    assert task.rtvi.messages[0]["type"] == "session"
    await task.rtvi.emit("on_client_message", SimpleNamespace(type="ui-ready"))
    assert task.frames == []
    ready.set()
    await spin_until(lambda: len(task.frames) == 1)
    await task.rtvi.emit("on_client_ready")
    await task.rtvi.emit("on_client_message", SimpleNamespace(type="ui-ready"))
    await manager.offer(OfferRequest(sdp="offer", type="offer", pc_id=peer.pc_id))
    assert len(task.frames) == len(runs) == 1
    result = await manager.end(manager._live.call_sid)
    assert result.saved
    assert peer.closed and ACTIVE_SESSIONS == {}


@pytest.mark.parametrize("code", ["hi", "kn", "mr", "te"])
@pytest.mark.asyncio
async def test_tagged_greeting_sets_language_before_speech_and_saves_no_tags(setup_manager, code):
    from pipecat.frames.frames import TTSSpeakFrame, TTSUpdateSettingsFrame
    from app.services.language import to_language_enum

    manager, _peer, task, ready, _runs = setup_manager
    runtime_config._store._config.prompt.greeting_text = f"<{code}> Hello <{code}> there."
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    call_sid = manager._live.call_sid
    await task.rtvi.emit("on_client_ready")
    await task.rtvi.emit("on_client_message", SimpleNamespace(type="ui-ready"))
    ready.set()
    await spin_until(lambda: len(task.frames) == 2)
    assert isinstance(task.frames[0], TTSUpdateSettingsFrame)
    assert task.frames[0].delta.language == to_language_enum(code)
    assert isinstance(task.frames[1], TTSSpeakFrame)
    assert task.frames[1].text == "Hello there."
    await manager.end(call_sid)
    saved = await storage.get_call_log(call_sid)
    assert saved["transcript"][0]["text"] == "Hello there."


@pytest.mark.asyncio
async def test_parallel_offer_rejected_even_during_initialization(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    peer.initialize_gate = asyncio.Event()
    first = asyncio.create_task(manager.offer(OfferRequest(sdp="offer", type="offer")))
    await spin_until(lambda: manager._live is not None)
    with pytest.raises(HTTPException) as failure:
        await manager.offer(OfferRequest(sdp="other", type="offer"))
    assert failure.value.status_code == 409
    peer.initialize_gate.set()
    await first
    await manager.close()


@pytest.mark.asyncio
async def test_end_and_disconnect_share_one_save_and_normal_result(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    call_sid = manager._live.call_sid
    with patch("app.storage.persist_call_log", wraps=storage.persist_call_log) as save:
        first, second, _ = await asyncio.gather(manager.end(call_sid), manager.end(call_sid), peer.emit("closed"))
        assert first == second == await manager.end(call_sid)
        assert save.await_count == 1
    log = await storage.get_call_log(call_sid)
    assert log["conversation_outcome"] == "user_disconnected"
    assert log["error"] is None
    assert ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_provider_error_is_not_reclassified_as_disconnect(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    live = manager._live
    runs[0]["hooks"].on_error("tts_provider_error")
    await peer.emit("closed")
    result = await live.cleanup
    assert result.saved
    log = await storage.get_call_log(live.call_sid)
    assert log["conversation_outcome"] == "pipeline_error"


@pytest.mark.asyncio
async def test_save_failure_releases_slot_and_returns_no_history_link(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    with patch("app.storage.persist_call_log", new=AsyncMock(side_effect=OSError("disk full"))):
        result = await manager.end(manager._live.call_sid)
    assert not result.saved and result.history_url is None
    assert manager._live is None and peer.closed
    assert result.call_sid in ACTIVE_SESSIONS
    assert (await manager.end(result.call_sid)).saved
    assert ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_slow_runner_eventually_saves_once_after_end_wait_expires(setup_manager):
    manager, peer, task, *_ = setup_manager
    release = asyncio.Event()

    async def slow_pipeline(**kwargs):
        kwargs["hooks"].on_task(task, asyncio.Event().wait)
        await task.finished.wait()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # Simulate asynchronous provider/processor cleanup.
        return PipelineExecutionResult(completed=True)

    manager._pipeline = slow_pipeline
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    live = manager._live
    live.snapshot["runtime_config"]["ops"]["shutdown_timeout_secs"] = 0.01
    with patch("app.storage.persist_call_log", wraps=storage.persist_call_log) as save, \
         patch("app.services.browser_session.finalize_call_log", wraps=finalize_call_log) as finalize:
        result = await manager.end(live.call_sid)
        assert not result.saved and "still ending" in result.error
        assert manager._live is live and save.await_count == 0
        with pytest.raises(HTTPException) as failure:
            await manager.offer(OfferRequest(sdp="other", type="offer"))
        assert failure.value.status_code == 409
        release.set()
        assert (await live.cleanup).saved
        assert (await manager.end(live.call_sid)).saved
        assert save.await_count == finalize.call_count == 1
    assert manager._live is None and not manager._stuck and ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_failed_save_retries_are_serialized_without_refinalizing(setup_manager):
    manager, _, _, *_ = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    call_sid = manager._live.call_sid
    actual_save = storage.persist_call_log
    with patch("app.storage.persist_call_log", new=AsyncMock(side_effect=OSError("disk full"))):
        assert not (await manager.end(call_sid)).saved
    with patch("app.services.browser_session.finalize_call_log") as finalize, \
         patch("app.storage.persist_call_log", wraps=actual_save) as save:
        first, second = await asyncio.gather(manager.end(call_sid), manager.end(call_sid))
        assert first.saved and second.saved
        assert save.await_count == 1 and finalize.call_count == 0
    assert ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_canceled_save_retry_does_not_cancel_file_transaction(setup_manager):
    manager, *_ = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    call_sid = manager._live.call_sid
    with patch("app.storage.persist_call_log", new=AsyncMock(side_effect=OSError("disk full"))):
        await manager.end(call_sid)
    entered, release = asyncio.Event(), asyncio.Event()
    actual = storage.persist_call_log

    async def slow_save(log):
        entered.set()
        await release.wait()
        return await actual(log)

    with patch("app.storage.persist_call_log", new=slow_save):
        request = asyncio.create_task(manager.end(call_sid))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        assert (await manager.end(call_sid)).saved
    assert call_sid not in ACTIVE_SESSIONS


@pytest.mark.asyncio
async def test_bad_initialization_is_cleaned_and_reported(setup_manager):
    manager, peer, *_ = setup_manager
    with pytest.raises(HTTPException) as failure:
        await manager.offer(OfferRequest(sdp="bad", type="offer"))
    assert failure.value.status_code == 500
    assert manager._live is None and peer.closed and ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_abandoned_offer_cleanup_outlives_http_request(setup_manager):
    manager, peer, *_ = setup_manager
    peer.initialize_gate = asyncio.Event()
    request = asyncio.create_task(manager.offer(OfferRequest(sdp="offer", type="offer")))
    await spin_until(lambda: manager._live is not None)
    live = manager._live
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    await live.cleanup
    assert peer.closed and manager._live is None and ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_unknown_peer_and_missing_voice_never_start_pipeline(setup_manager):
    manager, peer, task, ready, runs = setup_manager
    with pytest.raises(HTTPException) as failure:
        await manager.offer(OfferRequest(sdp="offer", type="offer", pc_id="unknown"))
    assert failure.value.status_code == 404
    with pytest.raises(HTTPException):
        await manager.candidates(CandidateRequest(pc_id="unknown", candidates=[]))
    runtime_config._store._config.tts.voice_id = ""
    with pytest.raises(HTTPException) as failure:
        await manager.offer(OfferRequest(sdp="offer", type="offer"))
    assert failure.value.status_code == 409
    assert manager._live is None and runs == []


@pytest.mark.asyncio
async def test_assembly_failure_returns_error_and_releases_resources(setup_manager):
    manager, peer, *_ = setup_manager

    async def failed(**kwargs):
        raise RuntimeError("assembly failed")

    manager._pipeline = failed
    with pytest.raises(HTTPException) as failure:
        await manager.offer(OfferRequest(sdp="offer", type="offer"))
    assert failure.value.status_code == 500
    assert peer.closed and manager._live is None and ACTIVE_SESSIONS == {}


@pytest.mark.asyncio
async def test_readiness_timeout_ends_and_records_failed_session(setup_manager):
    manager, peer, *_ = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    live = manager._live
    live.watchdog.cancel()
    await asyncio.gather(live.watchdog, return_exceptions=True)
    live.deadline = time.monotonic()
    live.watchdog = asyncio.create_task(manager._watch_readiness(live))
    await spin_until(lambda: live.cleanup is not None)
    result = await live.cleanup
    assert result.saved and "timed out" in result.error
    assert (await storage.get_call_log(live.call_sid))["conversation_outcome"] == "pipeline_error"
    assert manager._live is None


@pytest.mark.asyncio
async def test_cancellation_failure_still_stops_runner_and_saves(setup_manager):
    manager, peer, task, *_ = setup_manager
    await manager.offer(OfferRequest(sdp="offer", type="offer"))
    live = manager._live
    task.cancel = AsyncMock(side_effect=RuntimeError("cancellation failed"))
    result = await manager.end(live.call_sid)
    assert live.runner.done() and peer.closed and result.saved
    assert result.error is not None and ACTIVE_SESSIONS == {}
