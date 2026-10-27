"""Deepgram Flux STT that connects in the background, so transport startup can proceed while the provider connects.

Root cause (verified in the installed package): ``DeepgramFluxSTTBase.start()``
ends with ``await self._connect()``, and ``STTService.process_frame`` only pushes
``StartFrame`` downstream *after* ``start()`` returns. So the opening greeting —
queued behind ``StartFrame`` — waits ~1s for the Flux websocket to connect even
though STT isn't needed while the bot greets.

This subclass runs the normal base ``start()`` setup but kicks the connect off as
a background task, letting ``StartFrame`` (and the greeting) flow straight to TTS
while Flux connects. The user is muted through the greeting, and ``run_stt``
already drops audio safely if any arrives before the socket is open, so nothing
is lost. Selected via the ``stt.defer_connect_on_start`` runtime-config flag.

Lifecycle: the background connect is tracked and cancelled on stop/cancel so it
never outlives the call. Intentional cancellation is swallowed (no spurious
ErrorFrame); only a real connect failure surfaces an ErrorFrame. ``start()`` is
idempotent — a second call never schedules a duplicate connect.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from loguru import logger
from pipecat.frames.frames import CancelFrame, EndFrame, ErrorFrame, Frame, StartFrame
from pipecat.services.deepgram.flux.base import DeepgramFluxSTTBase
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService


class DeferredConnectDeepgramFluxSTTService(DeepgramFluxSTTService):
    """A Flux STT service whose websocket connects in the background on start."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._deferred_connect_task: Any | None = None
        self._deferred_started: bool = False
        self._warned_early_audio: bool = False
        # Timestamps for correlating greeting latency with STT readiness.
        self.connect_started_at: float | None = None
        self.connected_at: float | None = None
        self.connect_failed_at: float | None = None
        self.connect_cancelled_at: float | None = None
        self._connection_ready = asyncio.Event()
        self._connection_error: str | None = None

    async def wait_until_ready(self) -> None:
        """Wait for the provider socket or report its startup failure."""
        await self._connection_ready.wait()
        if self._connection_error is not None:
            raise RuntimeError(self._connection_error)

    async def start(self, frame: StartFrame) -> None:
        # Invoke DeepgramFluxSTTBase.start's *setup* without its trailing blocking
        # ``await self._connect()`` by calling its super() directly, then connect
        # in the background so StartFrame propagation is not blocked.
        await super(DeepgramFluxSTTBase, self).start(frame)
        if self._deferred_started:
            logger.debug("Deferred Flux STT already started; not scheduling another connect")
            return
        self._deferred_started = True
        self.connect_started_at = time.time()
        logger.info("Deferred Flux STT connect starting in background")
        self._deferred_connect_task = self.create_task(self._background_connect())

    async def stop(self, frame: EndFrame) -> None:
        await self._cancel_deferred_connect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._cancel_deferred_connect()
        await super().cancel(frame)

    async def _background_connect(self) -> None:
        try:
            await self._connect()
            if not self._transport_is_active():
                raise RuntimeError("Deepgram connection did not become active")
            self.connected_at = time.time()
            self._connection_ready.set()
            elapsed_ms = round((self.connected_at - (self.connect_started_at or self.connected_at)) * 1000.0, 1)
            logger.info(
                "Deferred Flux STT connect completed (ready for audio)", connect_ms=elapsed_ms
            )
        except asyncio.CancelledError:
            # Intentional shutdown — do NOT surface an ErrorFrame.
            self.connect_cancelled_at = time.time()
            self._connection_error = "Speech recognition startup canceled"
            self._connection_ready.set()
            logger.info("Deferred Flux STT connect cancelled during shutdown")
            raise
        except Exception as exc:  # a real connect failure must surface clearly
            self.connect_failed_at = time.time()
            self._connection_error = "Speech recognition startup failed"
            self._connection_ready.set()
            logger.error("Deferred Flux STT connect failed: {err}", err=str(exc))
            await self.push_error_frame(ErrorFrame(f"Deepgram Flux STT connect failed: {exc}"))

    async def _cancel_deferred_connect(self) -> None:
        task = self._deferred_connect_task
        self._deferred_connect_task = None
        if task is not None and not task.done():
            logger.info("Cancelling pending deferred Flux STT connect")
            await self.cancel_task(task)

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        # The base safely drops audio while the socket isn't open, but silently.
        # Log once so the narrow "greeting unmuted before Flux connected" window
        # is visible during live validation rather than looking like dead STT.
        if not self._transport_is_active() and any(audio) and not self._warned_early_audio:
            self._warned_early_audio = True
            logger.warning(
                "Deferred Flux STT: user audio arrived before the websocket was "
                "ready; dropping non-silent input until connected"
            )
        async for frame in super().run_stt(audio):
            yield frame
