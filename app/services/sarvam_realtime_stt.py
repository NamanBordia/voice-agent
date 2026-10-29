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
from loguru import logger

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
        self._hold_seconds = 0.0
        self._pending_finals: list[tuple[str, Language | None]] = []
        self._release_task: asyncio.Task[None] | None = None
        self._releasing = False
        self._speech_started_at: float | None = None
        self._speech_ended_at: float | None = None
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

    def configure_turn_hold(self, milliseconds: int) -> None:
        """Hold interview finals briefly using the existing snapshotted turn-stop setting."""
        self._hold_seconds = max(0, min(milliseconds, 3000)) / 1000

    async def _cancel_release(self) -> None:
        task, self._release_task = self._release_task, None
        if task is not None and task is not asyncio.current_task():
            if not self._releasing:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _release_pending(self) -> None:
        if self._closing or self._failed or self._utterance_open:
            return
        chunks, self._pending_finals = self._pending_finals, []
        if not chunks or not self.accept_turn():
            return
        text = " ".join(chunk for chunk, _ in chunks)
        detected = next((language for _, language in reversed(chunks) if language is not None), None)
        self.last_detected_language = detected.value.split("-")[0] if detected else None
        if self.commit_turn is None or not await self.commit_turn(self, text):
            return
        logger.debug("Sarvam accepted final released", provider_turn_index=self._turn_index,
            chunks=len(chunks), hold_ms=self._hold_seconds * 1000, released_at=time.monotonic())
        await self.push_frame(TranscriptionFrame(text, "", time_now_iso8601(), finalized=True, language=detected))
        await self.push_frame(UserStoppedSpeakingFrame())

    async def _release_after_hold(self) -> None:
        try:
            await asyncio.sleep(self._hold_seconds)
            self._releasing = True
            await self._release_pending()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Preserve failed checkpoints; do not silently lose an asynchronous release.
            await self._fail("accepted transcript release failed")
        finally:
            self._releasing = False

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
        await self._cancel_release()
        self._pending_finals.clear()
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
            await self._cancel_release()
            self._utterance_open = True
            self._turn_index += 1
            self._speech_started_at = time.monotonic()
            self._speech_ended_at = None
            logger.debug("Sarvam speech started", provider_turn_index=self._turn_index,
                started_at=self._speech_started_at, buffered_chunks=len(self._pending_finals))
            await self._call_event_handler("on_start_of_turn")
            if self.accept_turn():
                await self.push_frame(UserStartedSpeakingFrame())
            else:
                self._pending_finals.clear()
        elif event == "transcript.partial":
            if self._utterance_open and self.accept_turn() and message.get("text", "").strip():
                await self.push_frame(InterimTranscriptionFrame(
                    message["text"], "", time_now_iso8601(),
                    language=to_language_enum(message.get("language") or self._config.language_code),
                ))
        elif event == "vad.speech_end":
            self._speech_ended_at = time.monotonic()
            logger.debug("Sarvam speech ended", provider_turn_index=self._turn_index,
                ended_at=self._speech_ended_at)
            if self._utterance_open and self.accept_turn():
                await self.start_ttfb_metrics()
        elif event == "transcript.final":
            if not self._utterance_open:
                return
            self._utterance_open = False
            text = " ".join(str(message.get("text") or "").split())
            logger.debug("Sarvam final received", provider_turn_index=self._turn_index,
                final_at=time.monotonic(), speech_ended_at=self._speech_ended_at,
                accepted=self.accept_turn(), characters=len(text))
            if not self.accept_turn():
                self._pending_finals.clear()
                return
            if re.search(r"\w", text):
                self._pending_finals.append((text, to_language_enum(message.get("language") or self._config.language_code)))
            if sum(len(chunk) + 1 for chunk, _ in self._pending_finals) > 10000:
                await self._fail("accepted transcript exceeded the interview answer limit")
                return
            if not self._pending_finals:
                return
            # Speech resuming during this window cancels release and retains the
            # accepted chunks. No reservation, memory or grading happens until release.
            if self._hold_seconds:
                self._release_task = asyncio.create_task(self._release_after_hold(), name="sarvam-final-hold")
            else:
                await self._release_pending()
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
        await self._cancel_release()
        self._pending_finals.clear()
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
