"""Bounded final-answer streaming for normal and speculative voice replies."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.cerebras.llm import CerebrasLLMService


class VoiceCerebrasLLMService(CerebrasLLMService):
    """Apply one total deadline, disable SDK retries, and close draft streams."""

    def __init__(self, *, request_timeout_seconds: float, **kwargs: Any) -> None:
        super().__init__(retry_timeout_secs=request_timeout_seconds, **kwargs)
        self.request_timeout_seconds = request_timeout_seconds
        self._client = self._client.with_options(timeout=request_timeout_seconds, max_retries=0)

    async def _process_context(self, context: LLMContext) -> None:
        deadline = asyncio.timeout(self.request_timeout_seconds)
        try:
            async with deadline:
                await super()._process_context(context)
        except TimeoutError as exc:
            if not deadline.expired():
                raise
            raise TimeoutError(
                "Cerebras completion exceeded the configured "
                f"{self.request_timeout_seconds:g}-second total request timeout."
            ) from exc

    async def stream_draft(self, messages: list[dict[str, Any]]) -> AsyncIterator[Any]:
        """Use the same settings and deadline as the normal in-pipeline response."""
        context = LLMContext(messages=messages)
        params = self.get_llm_adapter().get_llm_invocation_params(
            context, convert_developer_to_user=not self.supports_developer_role,
        )
        stream = None
        try:
            async with asyncio.timeout(self.request_timeout_seconds):
                stream = await self._client.chat.completions.create(**self.build_chat_completion_params(params))
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].finish_reason:
                        logger.info("Cerebras draft finished", model=self._settings.model,
                                    finish_reason=chunk.choices[0].finish_reason)
                    yield chunk
        finally:
            if stream is not None:
                await stream.close()
