import os
import time
import pytest
from dotenv import load_dotenv

load_dotenv(override=True)

from pipecat.frames.frames import TextFrame, AudioRawFrame, EndFrame, StartFrame, Frame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.config import get_settings
from app.services.runtime_config import load_runtime_config, build_effective_snapshot
from app.services.pipecat_pipeline import _build_tts

class MockSourceNode(FrameProcessor):
    """Pushes test frames immediately after pipeline startup."""
    def __init__(self, frames):
        super().__init__()
        self._test_frames = frames

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)
        if isinstance(frame, StartFrame):
            for f in self._test_frames:
                if isinstance(f, EndFrame):
                    # Queue EndFrame on the task to trigger graceful pipeline runner shutdown
                    await self.pipeline_task.queue_frame(f)
                else:
                    await self.push_frame(f)

class MetricsCollector(FrameProcessor):
    """Intercepts frames to calculate real-time TTFA & Latency."""
    def __init__(self):
        super().__init__()
        self.start_time = None
        self.first_byte_time = None
        self.total_frames = 0
        self.received_audio_bytes = 0
        self.captured = False

    async def process_frame(self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        await super().process_frame(frame, direction)
        
        if self.start_time is None:
            self.start_time = time.time()
            
        if isinstance(frame, AudioRawFrame):
            if self.first_byte_time is None:
                self.first_byte_time = time.time()
                self.captured = True
            self.total_frames += 1
            self.received_audio_bytes += len(frame.audio)
            
        elif isinstance(frame, EndFrame):
            end_time = time.time()
            if self.first_byte_time:
                ttfb = self.first_byte_time - self.start_time
                total_latency = end_time - self.start_time
                print(f"\n[TTS METRICS] TTFA (Time to First Audio): {ttfb:.3f}s | Total: {total_latency:.3f}s")
                print(f"[TTS METRICS] Audio Received: {self.received_audio_bytes} bytes in {self.total_frames} frames")
            else:
                print(f"\n[TTS METRICS] Audio frame never received. Total execution: {end_time - self.start_time:.3f}s")

        await self.push_frame(frame, direction)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_tts_metrics():
    """Test Sarvam streaming TTS API and measure synthesis metrics in isolation."""
    settings = get_settings()
    snapshot = build_effective_snapshot(await load_runtime_config(settings))
    assert snapshot["TTS_VOICE_ID"], "Choose a voice in /config before running this live check"
    
    tts = _build_tts(snapshot, settings)
    source = MockSourceNode([
        TextFrame("Quantum computing is a rapidly-emerging technology that harnesses the laws of quantum mechanics to solve problems too complex for classical computers. It relies on qubits, which can exist in multiple states simultaneously."),
        EndFrame()
    ])
    collector = MetricsCollector()
    
    pipeline = Pipeline([source, tts, collector])
    task = PipelineTask(pipeline, params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000))
    runner = PipelineRunner()
    
    print("\n--- Running Isolated Live TTS Test ---")
    await runner.run(task)
    
    assert collector.captured is True
    assert collector.received_audio_bytes > 0
