"""Local runtime configuration and isolated per-conversation snapshots."""

from typing import Any

from app.config import Settings, V1_SAMPLE_RATE
from app.models.runtime_config_models import RuntimeConfig
from app.services.config_store import ConfigStore

_store = ConfigStore()


def get_cached_runtime_config() -> RuntimeConfig | None:
    """Return a copy of the loaded configuration."""
    return _store.get()


async def load_runtime_config(settings: Settings | None = None) -> RuntimeConfig:
    """Return loaded settings; initialize only when the cache is absent."""
    return _store.get() or await _store.load()


async def seed_runtime_config_if_missing(settings: Settings | None = None) -> RuntimeConfig:
    """Load and validate settings at startup."""
    return await _store.load()


async def save_runtime_config(config: RuntimeConfig) -> RuntimeConfig:
    """Serialize a validated settings save and refresh the cache."""
    return await _store.save(config)


async def close_runtime_config() -> None:
    """Finish pending saves before shutdown."""
    await _store.close()


def build_effective_snapshot(config: RuntimeConfig) -> dict[str, Any]:
    """Copy settings into the existing pipeline's per-conversation contract."""
    return {
        "runtime_config": config.model_dump(mode="json"),
        "SYSTEM_PROMPT": config.prompt.system_prompt,
        "GREETING_TEXT": config.prompt.greeting_text,
        "LLM_MODEL": config.llm.model,
        "LLM_TEMPERATURE": config.llm.temperature,
        "TTS_VOICE_ID": config.tts.voice_id,
        "TTS_MODEL": config.tts.model,
        "SAMPLE_RATE": V1_SAMPLE_RATE,
        "snapshot_version": "browser-v1",
    }
