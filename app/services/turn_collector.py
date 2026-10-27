"""Committed-turn collector for Deepgram Flux/Pipecat turn events."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Literal

from app.models.conversation_models import TurnCollectionResult, TurnEvent, TurnInput

DEFAULT_DUPLICATE_WINDOW_SECS = 0.25


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _has_speech_content(text: str) -> bool:
    return re.search(r"\w", text, flags=re.UNICODE) is not None


def _metadata_provider_turn_index(metadata: dict[str, Any]) -> int | None:
    for key in ("provider_turn_index", "turn_index"):
        value = metadata.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


class TurnCollector:
    """Convert normalized STT events into one committed TurnInput per user turn."""

    def __init__(
        self,
        *,
        call_sid: str,
        stream_sid: str | None = None,
        duplicate_window_secs: float = DEFAULT_DUPLICATE_WINDOW_SECS,
        source: Literal["deepgram_flux_end_of_turn", "pipecat_turn_complete"] = "deepgram_flux_end_of_turn",
    ) -> None:
        self._source = source
        self._call_sid = call_sid
        self._stream_sid = stream_sid
        self._duplicate_window_secs = duplicate_window_secs
        self._turn_index = 0
        self._buffered_final_text: str | None = None
        self._buffered_final_timestamp: datetime | None = None
        self._buffered_final_metadata: dict[str, Any] = {}
        self._last_committed_text: str | None = None
        self._last_committed_at: datetime | None = None
        self._last_provider_turn_index: int | None = None

    def observe(
        self,
        event: TurnEvent,
        *,
        phase: str,
        greeting_pending: bool = False,
    ) -> TurnCollectionResult:
        """Observe one normalized event and optionally return a committed turn."""

        provider_turn_index = _metadata_provider_turn_index(event.metadata)
        if greeting_pending and event.event_type in {"final_transcript", "end_of_turn"}:
            self._clear_pending_turn()
            return TurnCollectionResult(
                ignored_reason="greeting_pending",
                commit_source=(
                    self._source
                    if event.event_type == "end_of_turn"
                    else None
                ),
                commit_timestamp=event.timestamp if event.event_type == "end_of_turn" else None,
                provider_turn_index=provider_turn_index,
                greeting_pending_suppressed=True,
            )

        if event.event_type == "final_transcript":
            if self._is_late_final_after_commit(event):
                return TurnCollectionResult(
                    ignored_reason="late_final_after_commit",
                    provider_turn_index=provider_turn_index,
                    duplicate_suppressed=True,
                )
            self._buffer_final_transcript(event)
            return TurnCollectionResult(provider_turn_index=provider_turn_index)

        if event.event_type != "end_of_turn":
            return TurnCollectionResult(provider_turn_index=provider_turn_index)

        commit_timestamp = event.timestamp
        event_text = _normalize_text(event.text or "")
        buffered_text = _normalize_text(self._buffered_final_text or "")
        if event_text:
            text = event_text
            provider_metadata = dict(event.metadata)
            if buffered_text and buffered_text != event_text:
                provider_metadata["buffered_final_text_mismatch"] = buffered_text
        else:
            text = buffered_text
            provider_metadata = dict(self._buffered_final_metadata)
            provider_metadata.update(event.metadata)
        if provider_turn_index is not None:
            provider_metadata["provider_turn_index"] = provider_turn_index

        normalized_text = _normalize_text(text)
        if not normalized_text or not _has_speech_content(normalized_text):
            self._clear_pending_turn()
            return TurnCollectionResult(
                ignored_reason="empty_text",
                commit_source=self._source,
                commit_timestamp=commit_timestamp,
                provider_turn_index=provider_turn_index,
            )

        if self._is_duplicate(
            normalized_text=normalized_text,
            committed_at=commit_timestamp,
            provider_turn_index=provider_turn_index,
        ):
            self._clear_pending_turn()
            return TurnCollectionResult(
                ignored_reason="duplicate_turn",
                commit_source=self._source,
                commit_timestamp=commit_timestamp,
                provider_turn_index=provider_turn_index,
                duplicate_suppressed=True,
            )

        self._turn_index += 1
        turn = TurnInput(
            call_sid=self._call_sid,
            stream_sid=self._stream_sid,
            turn_index=self._turn_index,
            text=text,
            normalized_text=normalized_text,
            phase=phase,
            source=self._source,
            started_at=self._buffered_final_timestamp,
            committed_at=commit_timestamp,
            provider_metadata=provider_metadata,
        )
        self._last_committed_text = normalized_text
        self._last_committed_at = commit_timestamp
        self._last_provider_turn_index = provider_turn_index
        self._clear_pending_turn()
        return TurnCollectionResult(
            turn=turn,
            commit_source=self._source,
            commit_timestamp=commit_timestamp,
            provider_turn_index=provider_turn_index,
        )

    def _buffer_final_transcript(self, event: TurnEvent) -> None:
        text = _normalize_text(event.text or "")
        if not text:
            return
        self._buffered_final_text = text
        self._buffered_final_timestamp = event.timestamp
        self._buffered_final_metadata = dict(event.metadata)

    def _is_duplicate(
        self,
        *,
        normalized_text: str,
        committed_at: datetime,
        provider_turn_index: int | None,
    ) -> bool:
        if (
            provider_turn_index is not None
            and self._last_provider_turn_index is not None
            and provider_turn_index == self._last_provider_turn_index
        ):
            return True
        if self._last_committed_text != normalized_text or self._last_committed_at is None:
            return False
        elapsed = abs((committed_at - self._last_committed_at).total_seconds())
        return elapsed <= self._duplicate_window_secs

    def _is_late_final_after_commit(self, event: TurnEvent) -> bool:
        if self._last_committed_at is None:
            return False
        elapsed = (event.timestamp - self._last_committed_at).total_seconds()
        return 0 <= elapsed <= self._duplicate_window_secs

    def _clear_pending_turn(self) -> None:
        self._buffered_final_text = None
        self._buffered_final_timestamp = None
        self._buffered_final_metadata = {}


def utc_now() -> datetime:
    """Return a timezone-aware timestamp for turn events."""

    return datetime.now(timezone.utc)
