"""Local warmup: runs offline (no provider network), never raises."""

import asyncio

import pipecat.audio.vad.silero as silero_mod

from app.config import get_settings
from app.services.warmup import run_local_warmup


def test_warmup_runs_without_provider_network():
    # Silero VAD is a local (bundled) model — no provider keys/sockets needed.
    results = asyncio.run(run_local_warmup(get_settings()))
    assert results.get("silero_vad") == "ok"


def test_warmup_handles_failure_without_raising(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("no model available")

    monkeypatch.setattr(silero_mod, "SileroVADAnalyzer", _Boom)
    results = asyncio.run(run_local_warmup(get_settings()))
    assert results["silero_vad"].startswith("failed")
