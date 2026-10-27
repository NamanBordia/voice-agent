"""Serialized, cancellation-safe settings writes and cache ownership."""

from __future__ import annotations

import asyncio

from app.models.runtime_config_models import RuntimeConfig
from app import storage
from loguru import logger


class ConfigStore:
    """One process-local config cache, updated only after durable replacement."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._config: RuntimeConfig | None = None
        self._writes: set[asyncio.Task[RuntimeConfig]] = set()

    def get(self) -> RuntimeConfig | None:
        """Return a copy so callers cannot mutate the authoritative cache."""
        return self._config.model_copy(deep=True) if self._config is not None else None

    async def load(self) -> RuntimeConfig:
        """Load once on startup; malformed settings fail rather than reset."""
        async with self._lock:
            await storage.initialize_storage()
            config = await storage.read_config()
            if config is None:
                config = RuntimeConfig.default()
                await storage.write_config(config)
            self._config = config
            return config.model_copy(deep=True)

    async def save(self, config: RuntimeConfig) -> RuntimeConfig:
        """Finish replacement/cache update even when the request is canceled."""
        validated = RuntimeConfig.model_validate(config.model_dump(mode="json"))

        async def write() -> RuntimeConfig:
            async with self._lock:
                await storage.write_config(validated)
                self._config = validated
                return validated.model_copy(deep=True)

        task = asyncio.create_task(write())
        self._writes.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _finished(self, task: asyncio.Task[RuntimeConfig]) -> None:
        self._writes.discard(task)
        if not task.cancelled():
            if task.exception() is not None:
                logger.error("Runtime settings write failed; previous settings remain active")

    async def close(self) -> None:
        """Drain any settings writes before application shutdown."""
        if self._writes:
            await asyncio.gather(*self._writes, return_exceptions=True)
