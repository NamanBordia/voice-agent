"""Local startup warmup to remove first-call cold-start cost.

The first call after a cold process pays one-time local init: loading the Silero
VAD model (onnxruntime session + weights) and importing the heavy audio stack.
Doing it once at startup keeps the first real greeting fast.

Strictly **local**: no provider websockets are opened and no billable provider
synthesis is run here. (Provider warmup is intentionally not implemented — it
would open startup sockets / incur cost; if ever added it must sit behind a
default-off flag.) Failures are logged clearly and never corrupt runtime state.
"""

from __future__ import annotations

from loguru import logger

from app.config import Settings, V1_SAMPLE_RATE


async def run_local_warmup(settings: Settings) -> dict[str, str]:
    """Warm local cold-start components. Never raises; returns a status map."""

    results: dict[str, str] = {}

    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        # Instantiate-and-discard: pays the import + model-load cost now so the
        # first per-call analyzer is cheap. The instance is stateful per stream,
        # so it is deliberately not reused.
        SileroVADAnalyzer(sample_rate=V1_SAMPLE_RATE)
        results["silero_vad"] = "ok"
    except Exception as exc:
        results["silero_vad"] = f"failed: {exc}"
        logger.warning("Local warmup (silero_vad) failed: {err}", err=str(exc))

    logger.info("Local warmup complete: {results}", results=results)
    return results
