"""Replace only the interview LLM slot, preserving aggregation and speech frames."""

from collections.abc import Callable
from copy import deepcopy
from pipecat.frames.frames import (
    BotStoppedSpeakingFrame, Frame, LLMContextFrame, LLMFullResponseEndFrame,
    LLMFullResponseStartFrame, LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from app.interviews.service import InterviewBinding


class InterviewResponseProcessor(FrameProcessor):
    """Consume the sole context trigger; internal JSON never enters the pipeline."""

    def __init__(self, binding: InterviewBinding, on_finished: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.binding, self.on_finished = binding, on_finished
        self._generating = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Emit the existing LLM speech envelope after checkpoint validation."""
        await super().process_frame(frame, direction)
        if isinstance(frame, BotStoppedSpeakingFrame) and direction == FrameDirection.UPSTREAM:
            if await self.binding.delivered() and self.on_finished:
                self.on_finished()
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            if self._generating or not await self.binding.response_pending():
                return
            self._generating = True
            try:
                await self.push_frame(LLMFullResponseStartFrame())
                spoken = await self.binding.respond(deepcopy(frame.context.messages))
                await self.push_frame(LLMTextFrame(spoken))
            except Exception as exc:
                await self.push_error(error_msg=self.binding.failure_message(exc))
            finally:
                await self.push_frame(LLMFullResponseEndFrame())
                self._generating = False
            return
        await self.push_frame(frame, direction)
