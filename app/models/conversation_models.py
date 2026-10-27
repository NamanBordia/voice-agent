"""JSON-safe turn models for committed user turns.

V3 keeps only the turn-collection types used by ``TurnCollector`` and the
pipeline. The former slot-flow conversation/action/state models were removed in
Phase 1 (recoverable from git history).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class TurnEvent(BaseModel):
    """Normalized STT/turn event consumed by TurnCollector."""

    event_type: Literal[
        "update",
        "eager_end_of_turn",
        "end_of_turn",
        "turn_resumed",
        "final_transcript",
    ]
    text: str | None = None
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TurnInput(BaseModel):
    """Committed user turn passed to conversation logic."""

    call_sid: str
    stream_sid: str | None = None
    turn_index: int
    text: str
    normalized_text: str
    phase: str
    source: Literal[
        "deepgram_flux_end_of_turn",
        "pipecat_turn_complete",
        "manual_test",
        "fallback",
    ]
    started_at: datetime | None = None
    committed_at: datetime
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class TurnCollectionResult(BaseModel):
    """Result returned after TurnCollector observes one event."""

    turn: TurnInput | None = None
    ignored_reason: str | None = None
    commit_source: str | None = None
    commit_timestamp: datetime | None = None
    provider_turn_index: int | None = None
    duplicate_suppressed: bool = False
    greeting_pending_suppressed: bool = False
