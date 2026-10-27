"""Local WebRTC/RTVI wiring with silence only, not a voice/provider validation."""

import asyncio
import json
from pathlib import Path
import re

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
import pytest
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from app.config import get_settings
from app.models.browser_models import OfferRequest
from app.services.browser_session import BrowserSessionManager
from app.services.pipecat_pipeline import PipelineExecutionResult
from app.services import runtime_config


def test_installed_browser_sdk_matches_server_protocol():
    from pipecat.processors.frameworks.rtvi.models import PROTOCOL_VERSION
    types = (Path(__file__).resolve().parents[1] / "frontend/node_modules/@pipecat-ai/client-js/dist/index.d.ts").read_text()
    match = re.search(r'RTVI_PROTOCOL_VERSION = "([^"]+)"', types)
    assert match is not None and match.group(1) == PROTOCOL_VERSION == "1.3.0"


@pytest.mark.asyncio
async def test_actual_peer_handshake_rtvi_readiness_and_silent_audio():
    config = runtime_config._store.get()
    config.tts.voice_id = "shubh"
    runtime_config._store._config = config

    async def protocol_pipeline(*, transport, hooks, **kwargs):
        # No STT, LLM, or TTS providers. This only checks real transport plumbing.
        pipeline = Pipeline([transport.input(), transport.output()])
        task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=16000,
                                                          audio_out_sample_rate=16000))

        @transport.event_handler("on_client_connected")
        async def connected(*args):
            hooks.on_connected()

        @transport.event_handler("on_client_disconnected")
        async def disconnected(*args):
            hooks.on_disconnected()

        async def stt_ready():
            pass

        hooks.on_task(task, stt_ready)
        await PipelineRunner(handle_sigint=False).run(task)
        return PipelineExecutionResult(completed=True)

    manager = BrowserSessionManager(get_settings(), pipeline=protocol_pipeline)
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTransceiver("audio", direction="sendrecv")
    channel = peer.createDataChannel("pipecat")
    bot_ready, session_ready, audio_received = asyncio.Event(), asyncio.Event(), asyncio.Event()
    audio_task = None

    @channel.on("open")
    def opened():
        channel.send(json.dumps({"label": "rtvi-ai", "id": "ready", "type": "client-ready",
                                 "data": {"version": "1.3.0"}}))

    @channel.on("message")
    def message(raw):
        data = json.loads(raw)
        if data.get("type") == "bot-ready":
            bot_ready.set()
            channel.send(json.dumps({"label": "rtvi-ai", "id": "ui", "type": "client-message",
                                     "data": {"t": "ui-ready", "d": {}}}))
        if data.get("type") == "server-message" and data.get("data", {}).get("type") == "session":
            session_ready.set()

    @peer.on("track")
    def track_received(track):
        nonlocal audio_task
        async def receive():
            frame = await track.recv()
            assert frame.samples > 0
            audio_received.set()
        audio_task = asyncio.create_task(receive())

    try:
        async with asyncio.timeout(15):
            await peer.setLocalDescription(await peer.createOffer())
            answer = await manager.offer(OfferRequest(sdp=peer.localDescription.sdp, type="offer"))
            await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))
            await bot_ready.wait()
            await session_ready.wait()
            await audio_received.wait()
            live = manager._live
            for _ in range(100):
                if live.greeting_queued:
                    break
                await asyncio.sleep(0.01)
            assert live.greeting_queued
            assert (await manager.end(live.call_sid)).saved
    finally:
        await manager.close()
        await peer.close()
        if audio_task is not None:
            audio_task.cancel()
            await asyncio.gather(audio_task, return_exceptions=True)
