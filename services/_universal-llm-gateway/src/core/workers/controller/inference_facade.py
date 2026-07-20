"""Chat completion and token-count inference facade mixin."""

from collections.abc import AsyncIterator
from typing import Any


class InferenceFacadeMixin:
    """Delegates non-streaming and streaming chat inference to chat handlers."""

    async def inference(
        self,
        model_id: str,
        messages: list,
        parameters: dict,
        correlation_id: str | None = None,
    ):
        return await self._chat_non_streaming.inference(
            model_id, messages, parameters, correlation_id
        )

    async def generate_chat_completion(
        self, model_id: str, messages: list, correlation_id: str | None = None, **kwargs
    ):
        return await self._chat_non_streaming.generate_chat_completion(
            model_id, messages, correlation_id, **kwargs
        )

    async def count_tokens(
        self,
        model_id: str,
        message_or_prompt: list | str,
        use_cpu: bool,
        context_length: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict:
        return await self._chat_non_streaming.count_tokens(
            model_id, message_or_prompt, use_cpu, context_length, tools=tools
        )

    async def inference_stream(
        self,
        model_id: str,
        messages: list,
        parameters: dict,
        correlation_id: str | None = None,
    ) -> AsyncIterator[dict]:
        async for chunk in self._chat_streaming.inference_stream(
            model_id, messages, parameters, correlation_id
        ):
            yield chunk

    async def generate_chat_completion_stream(
        self, model_id: str, messages: list, correlation_id: str | None = None, **kwargs
    ) -> AsyncIterator[dict]:
        async for chunk in self._chat_streaming.generate_chat_completion_stream(
            model_id, messages, correlation_id, **kwargs
        ):
            yield chunk
