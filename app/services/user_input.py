"""One mute boundary for browser audio, transcripts, memory, and eager drafts."""

from dataclasses import replace
from typing import Any

from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class UserInputGate(FrameProcessor):
    """Send silence while muted and reject turns that began during bot speech."""

    def __init__(self) -> None:
        super().__init__()
        self.muted = True
        self.turn_allowed = False

    @property
    def accepting_turn(self) -> bool:
        """Whether this turn may update the transcript, memory, and response path."""
        return not self.muted and self.turn_allowed

    def set_muted(self, muted: bool) -> None:
        """Latch rejected speech until the provider starts a fresh turn after unmuting."""
        self.muted = muted
        if muted:
            self.turn_allowed = False

    def start_turn(self) -> None:
        """Capture the mute state at the provider's turn-start boundary."""
        self.turn_allowed = not self.muted

    async def process_frame(self, frame: Any, direction: FrameDirection) -> None:
        """Keep the provider's audio clock flowing without forwarding speaker echo."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM and self.muted and isinstance(frame, InputAudioRawFrame):
            frame = replace(frame, audio=bytes(len(frame.audio)))
        await self.push_frame(frame, direction)
