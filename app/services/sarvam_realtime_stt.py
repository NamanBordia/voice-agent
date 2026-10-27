"""Sarvam realtime protocol adapter for the project's pinned Pipecat 1.2.1."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from pipecat.frames.frames import (
    CancelFrame, EndFrame, ErrorFrame, Frame, InterimTranscriptionFrame,
    StartFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame,
)
from pipecat.services.settings import STTSettings
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language
from pipecat.utils.time import time_now_iso8601
from websockets.asyncio.client import connect

from app.services.language import to_language_enum


@dataclass
class SarvamRealtimeSettings(STTSettings):
    """Runtime snapshot settings; audio format is deliberately fixed."""

    model: str = "saaras:v3-realtime"
    language: Language | None = None  # language_code owns auto/fixed provider routing.
    language_code: str = "auto"
    stream_type: str = "fast"
    threshold: float = 0.3
    silence_duration_ms: int = 700
    min_speech_duration_ms: int = 250


class SarvamRealtimeSTTService(STTService):
    """Emit only accepted, once-per-utterance finals into memory and aggregation."""

    Settings = SarvamRealtimeSettings

    def __init__(self, *, api_key: str, settings: SarvamRealtimeSettings) -> None:
        super().__init__(sample_rate=16000, settings=settings)
        self._api_key = api_key
        self._config = settings
        self._socket: Any = None
        self._connection_task: asyncio.Task[Any] | None = None
        self._ready = asyncio.Event()
        self.connected_at: float | None = None
        self.last_detected_language: str | None = None
        self._closing = False
        self._failed = False
        self._utterance_open = False
        self._turn_index = 0
        self.accept_turn: Callable[[], bool] = lambda: False
        self.commit_turn: Callable[[Any, str], Awaitable[bool]] | None = None
        self._register_event_handler("on_start_of_turn", sync=True)
        # Retained hooks keep the inactive Flux telemetry code isolated.
        self._register_event_handler("on_eager_end_of_turn")
        self._register_event_handler("on_turn_resumed")

    @property
    def provider_turn_index(self) -> int:
        """Connection-local utterance identity, independent of transcript text."""
        return self._turn_index

    def can_generate_metrics(self) -> bool:
        """Enable Pipecat STT timing alongside the existing turn observer."""
        return True

    async def wait_until_ready(self) -> None:
        """Wait for Sarvam's session.begin, rather than just an open socket."""
        await self._ready.wait()

    async def start(self, frame: StartFrame) -> None:
        """Overlap provider setup with browser media setup."""
        await super().start(frame)
        self._connection_task = self.create_task(self._receive(), name="sarvam-realtime")

    async def _receive(self) -> None:
        cfg = self._config
        query = urlencode({
            "model": cfg.model, "language_code": cfg.language_code,
            "mode": "transcribe", "stream_type": cfg.stream_type,
            "endpointing": "vad", "encoding": "linear16", "sample_rate": 16000,
            "threshold": cfg.threshold, "silence_duration_ms": cfg.silence_duration_ms,
            "min_speech_duration_ms": cfg.min_speech_duration_ms,
        })
        try:
            async with connect(
                "wss://api.sarvam.ai/speech-to-text-realtime/ws?" + query,
                additional_headers={"Api-Subscription-Key": self._api_key},
                close_timeout=1,
            ) as socket:
                self._socket = socket
                async for message in socket:
                    await self.handle_message(json.loads(message))
            if not self._closing:
                await self._fail("provider closed the connection")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closing:
                await self._fail(f"connection failed ({type(exc).__name__}): {exc}")
        finally:
            self._socket = None
            self._ready.clear()

    async def _fail(self, reason: str) -> None:
        if self._failed or self._closing:
            return
        self._failed = True
        self._ready.clear()
        # Redact before ErrorFrame is logged, persisted, or sent to the browser.
        safe_reason = reason.replace(self._api_key, "[REDACTED]") if self._api_key else reason
        safe_reason = " ".join(safe_reason.split())[:300]
        await self.push_error_frame(ErrorFrame(f"Sarvam realtime STT failed: {safe_reason}", fatal=True))

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Map documented realtime events; partials never signal eager EOT."""
        if self._failed or self._closing:
            return
        event = message.get("event")
        if event == "session.begin":
            self.connected_at = time.time()
            self._ready.set()
            await self._call_event_handler("on_connected")
        elif event == "vad.speech_start":
            if self._utterance_open:
                return
            self._utterance_open = True
            self._turn_index += 1
            await self._call_event_handler("on_start_of_turn")
            if self.accept_turn():
                await self.push_frame(UserStartedSpeakingFrame())
        elif event == "transcript.partial":
            if self._utterance_open and self.accept_turn() and message.get("text", "").strip():
                await self.push_frame(InterimTranscriptionFrame(
                    message["text"], "", time_now_iso8601(),
                    language=to_language_enum(message.get("language") or self._config.language_code),
                ))
        elif event == "vad.speech_end":
            if self._utterance_open and self.accept_turn():
                await self.start_ttfb_metrics()
        elif event == "transcript.final":
            if not self._utterance_open:
                return
            self._utterance_open = False
            text = " ".join(str(message.get("text") or "").split())
            if not self.accept_turn() or not re.search(r"\w", text):
                return
            detected = to_language_enum(message.get("language") or self._config.language_code)
            self.last_detected_language = detected.value.split("-")[0] if detected else None
            if self.commit_turn is None or not await self.commit_turn(self, text):
                return
            await self.push_frame(TranscriptionFrame(
                text, "", time_now_iso8601(), finalized=True,
                language=detected,
            ))
            # Release only after an accepted final. Provider speech_end can precede
            # final by an arbitrary delay; releasing there risks an empty turn.
            await self.push_frame(UserStoppedSpeakingFrame())
        elif event == "error":
            await self._fail(f"provider error: {message.get('message') or 'unspecified rejection'}")
        elif event == "session.end" and not self._closing:
            await self._fail("provider ended the session unexpectedly")

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        """Send raw mono PCM without a WAV header or SDK buffering."""
        if self._ready.is_set() and self._socket is not None:
            try:
                await self._socket.send(json.dumps({
                    "event": "audio_input", "audio": base64.b64encode(audio).decode("ascii"),
                }))
            except Exception as exc:
                await self._fail(f"audio send failed ({type(exc).__name__}): {exc}")
        yield None

    async def _close(self) -> None:
        self._closing = True
        self._ready.clear()
        if self._connection_task is not None:
            await self.cancel_task(self._connection_task, timeout=None)
            self._connection_task = None

    async def stop(self, frame: EndFrame) -> None:
        """Close the receiver and its socket before normal teardown."""
        await self._close()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        """Cancel setup or reception without leaving a provider task alive."""
        await self._close()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        """Also drain setup when the pipeline fails before normal cancellation."""
        await self._close()
        await super().cleanup()
