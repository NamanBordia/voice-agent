"""Cerebras request parity, stream deadlines, and cancellation without provider calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.cerebras.llm import CerebrasLLMService

from app.config import get_settings
from app.runtime import build_config_snapshot
from app.services.pipecat_pipeline import _build_llm
from app.services.speculation_controller import make_cerebras_draft_factory


@pytest.mark.asyncio
async def test_normal_generation_has_a_total_deadline_and_no_sdk_retries():
    service = _build_llm(build_config_snapshot(get_settings()), get_settings())
    service.request_timeout_seconds = 0.01
    assert service._client.max_retries == 0

    async def stuck(*args):
        await asyncio.Event().wait()

    with patch.object(CerebrasLLMService, "_process_context", new=stuck):
        with pytest.raises(TimeoutError, match="Cerebras completion exceeded the configured 0.01-second"):
            await service._process_context(LLMContext(messages=[]))


@pytest.mark.asyncio
async def test_provider_timeout_reason_is_preserved_before_total_deadline():
    service = _build_llm(build_config_snapshot(get_settings()), get_settings())
    with patch.object(CerebrasLLMService, "_process_context", new=AsyncMock(side_effect=TimeoutError("socket read timed out"))):
        with pytest.raises(TimeoutError, match="socket read timed out"):
            await service._process_context(LLMContext(messages=[]))


@pytest.mark.asyncio
async def test_normal_generation_cancellation_is_not_reported_as_timeout():
    service = _build_llm(build_config_snapshot(get_settings()), get_settings())
    entered = asyncio.Event()

    async def stuck(*args):
        entered.set()
        await asyncio.Event().wait()

    with patch.object(CerebrasLLMService, "_process_context", new=stuck):
        running = asyncio.create_task(service._process_context(LLMContext(messages=[])))
        await entered.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running


@pytest.mark.asyncio
async def test_draft_uses_same_model_reasoning_and_budget_then_closes_on_timeout():
    service = _build_llm(build_config_snapshot(get_settings()), get_settings())
    service.request_timeout_seconds = 0.01

    class StalledStream:
        close = AsyncMock()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    stream = StalledStream()
    create = AsyncMock(return_value=stream)
    service._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    factory = make_cerebras_draft_factory(service)
    with pytest.raises(TimeoutError):
        async for _ in factory([{"role": "user", "content": "hello"}]):
            pass
    params = create.call_args.kwargs
    assert params["model"] == "qwen-3.8-27b"
    assert params["reasoning_effort"] == "none"
    assert params["max_completion_tokens"] == 1024 and params["stream"] is True
    stream.close.assert_awaited_once()
