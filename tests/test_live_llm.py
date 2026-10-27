import os
import time
import pytest
from dotenv import load_dotenv

# Ensure real credentials are used instead of CI mocks
load_dotenv(override=True)

from pipecat.frames.frames import (
    TextFrame,
    EndFrame,
    StartFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.aggregators.llm_context import LLMContext

from app.config import get_settings
from app.services.pipecat_pipeline import _build_llm as _build_cerebras_llm
from app.services.runtime_config import load_runtime_config, build_effective_snapshot

class MockSourceNode(FrameProcessor):
    """Pushes test frames immediately after pipeline startup."""
    def __init__(self, context_frame):
        super().__init__()
        self._context_frame = context_frame

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await self.push_frame(self._context_frame)

class MetricsCollector(FrameProcessor):
    """Intercepts frames to calculate real-time TTFB & Latency."""
    def __init__(self):
        super().__init__()
        self.start_time = None
        self.first_byte_time = None
        self.total_frames = 0
        self.captured = False

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)
        
        if self.start_time is None:
            self.start_time = time.time()
            
        if isinstance(frame, TextFrame):
            if self.first_byte_time is None:
                self.first_byte_time = time.time()
                self.captured = True
            self.total_frames += 1
            
        elif isinstance(frame, LLMFullResponseEndFrame):
            end_time = time.time()
            if self.first_byte_time:
                ttfb = self.first_byte_time - self.start_time
                total_latency = end_time - self.start_time
                print(f"\n[LLM METRICS] TTFB: {ttfb:.3f}s | Total: {total_latency:.3f}s | Text Frames: {self.total_frames}")
            else:
                print(f"\n[LLM METRICS] Target frame never received. Total execution: {end_time - self.start_time:.3f}s")
            
            # Request pipeline termination gracefully
            await self.pipeline_task.queue_frame(EndFrame())

        await self.push_frame(frame, direction)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_llm_metrics():
    """Test Cerebras LLM API and measure generation metrics in isolation."""
    settings = get_settings()
    snapshot = build_effective_snapshot(await load_runtime_config(settings))
    
    llm = _build_cerebras_llm(snapshot, settings)
    
    # We must use LLMContextFrame to trigger the Cerebras LLM generation flow in isolation.
    context = LLMContext(messages=[
        {"role": "user", "content": "Hello! Please reply with exactly 'Hello World' and nothing else."}
    ])
    context_frame = LLMContextFrame(context=context)
    
    source = MockSourceNode(context_frame)
    collector = MetricsCollector()
    
    pipeline = Pipeline([source, llm, collector])
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=False))
    runner = PipelineRunner()
    
    print("\n--- Running Isolated Live LLM Test ---")
    await runner.run(task)
    
    assert collector.captured is True
