"""Local configuration, snapshot isolation, and cancellation-safe saves."""

import asyncio
import json
from unittest.mock import AsyncMock, patch
import pytest
from pydantic import ValidationError

from app.models.runtime_config_models import RuntimeConfig
from app.services import runtime_config
from app.services.config_store import ConfigStore
from app.services.runtime_config import build_effective_snapshot


def test_defaults_and_snapshot_are_browser_specific():
    config = RuntimeConfig.default()
    snapshot = build_effective_snapshot(config)
    assert config.llm.model == "qwen-3.8-27b"
    assert config.llm.reasoning_effort == "none"
    assert config.tts.voice_id == "shubh"
    assert snapshot["GREETING_TEXT"] == config.prompt.greeting_text
    snapshot["runtime_config"]["llm"]["model"] = "changed"
    assert config.llm.model == "qwen-3.8-27b"
    assert snapshot["SAMPLE_RATE"] == 16000


def test_model_reasoning_combination_is_validated():
    with pytest.raises(ValidationError, match="GPT-OSS"):
        RuntimeConfig(llm={"model": "gpt-oss-120b", "reasoning_effort": "none"})
    assert RuntimeConfig(llm={"model": "gpt-oss-120b", "reasoning_effort": "low"})


def test_tts_buffer_rejects_edits_below_provider_minimum():
    assert RuntimeConfig().tts.min_buffer_size == 30
    with pytest.raises(ValidationError, match="greater than or equal to 30"):
        RuntimeConfig(tts={"min_buffer_size": 20})


@pytest.mark.parametrize("old_buffer", [1, 20, 29, 65])
@pytest.mark.asyncio
async def test_saved_tts_buffer_upgrades_on_load_and_can_be_saved(old_buffer):
    from app import storage
    await storage.initialize_storage()
    data = RuntimeConfig().model_dump(mode="json")
    data["tts"].update(min_buffer_size=old_buffer, voice_id="ritu", pace=1.2)
    path = storage.DATA_DIR / "runtime_config.json"
    original = json.dumps(data)
    path.write_text(original, encoding="utf-8")
    store = ConfigStore()
    loaded = await store.load()
    assert loaded.tts.min_buffer_size == max(30, old_buffer)
    assert loaded.tts.voice_id == "ritu" and loaded.tts.pace == 1.2
    assert path.read_text(encoding="utf-8") == original
    await store.save(loaded)
    assert (await ConfigStore().load()).tts.min_buffer_size == max(30, old_buffer)


@pytest.mark.asyncio
async def test_seed_edit_restart_and_cache_copy():
    store = ConfigStore()
    original = await store.load()
    original.tts.voice_id = "ritu"
    await store.save(original)
    copy = store.get()
    copy.tts.voice_id = "priya"
    assert store.get().tts.voice_id == "ritu"
    assert (await ConfigStore().load()).tts.voice_id == "ritu"


@pytest.mark.asyncio
async def test_failed_write_preserves_cache():
    store = ConfigStore()
    await store.load()
    changed = store.get()
    changed.tts.voice_id = "priya"
    with patch("app.storage.write_config", new=AsyncMock(side_effect=OSError("denied"))):
        with pytest.raises(OSError):
            await store.save(changed)
    assert store.get().tts.voice_id == "shubh"


@pytest.mark.asyncio
async def test_canceled_request_finishes_file_and_cache_transaction():
    store = ConfigStore()
    await store.load()
    entered, release = asyncio.Event(), asyncio.Event()
    from app import storage
    actual = storage.write_config

    async def slow(config):
        entered.set()
        await release.wait()
        await actual(config)

    changed = store.get()
    changed.tts.voice_id = "ritu"
    with patch("app.storage.write_config", new=slow):
        request = asyncio.create_task(store.save(changed))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        release.set()
        await store.close()
    assert store.get().tts.voice_id == "ritu"
    assert (await ConfigStore().load()).tts.voice_id == "ritu"
