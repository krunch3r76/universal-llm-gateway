"""
Base engine interface for inference_djinn.

Provides common functionality for all inference engines.
All engines NEVER apply defaults - parameters are used exactly as provided.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, TypeVar

from universal_logging import get_logger

if TYPE_CHECKING:
    from inference_djinn.utils.types import TokenCountResult

logger = get_logger(__name__)

T = TypeVar("T")


class BaseEngine(ABC):
    """Abstract base class for inference engines"""

    def __init__(self, model_path: str, **kwargs):
        self.model_path = model_path
        self.kwargs = kwargs
        self.loaded = False
        self.engine_type = "base"

    @abstractmethod
    async def load(self) -> None:
        """Load model using ONLY provided parameters - no defaults applied"""
        pass

    @abstractmethod
    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """
        Generate response using ONLY provided parameters - no defaults applied.

        Args:
            data: Request data with prompt/messages and generation params
            cancellation_event: Optional event to signal cancellation

        Returns:
            OpenAI-compliant completion response
        """
        pass

    @abstractmethod
    async def generate_stream(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Generate streaming response using ONLY provided parameters - no defaults applied.

        Args:
            data: Request data with prompt/messages and generation params
            cancellation_event: Optional event to signal cancellation of streaming.
                When set, the streaming will stop gracefully after the current chunk.
                Supports multiple cancellation sources:
                - Client disconnection (FastAPI raises asyncio.CancelledError)
                - Explicit cancellation via management API
                - Timeout enforcement
                - Resource limits (GPU memory, system constraints)

        Yields:
            Streaming chunks in OpenAI format with finish_reason="cancelled" on cancellation

        Raises:
            RuntimeError: If streaming fails (never yields error chunks)
        """
        pass

    @abstractmethod
    async def unload(self) -> None:
        """Unload model and free resources"""
        pass

    @abstractmethod
    def get_model_info(self) -> dict[str, Any]:
        """Get model information (informational only)"""
        pass

    def is_loaded(self) -> bool:
        """Check if engine is loaded and operational.

        Subclasses with health monitoring should override to reflect live status.
        """
        return self.loaded

    @abstractmethod
    async def count_tokens_for_messages(
        self,
        messages_or_prompt: list[dict[str, Any]] | str,
        use_cpu: bool = True,
        context_length: int | None = None,
    ) -> TokenCountResult:
        """
        Count tokens for chat messages or prompt string.

        All engines must support both input formats:
        - str: Formatted prompt string
        - list: Chat message list (converted using chat template)

        Args:
            messages_or_prompt: Either formatted prompt or message list
            use_cpu: If True, use CPU-based token counting. May be ignored by GPU-only engines.
            context_length: Context length for validation

        Returns:
            TokenCountResult with count, method, and success status
        """
        pass

    def _get_generation_params(self, data: dict[str, Any]) -> dict[str, Any]:
        """Extract generation parameters from request data - parameters must be at top level"""
        # Extract all parameters except for prompt/messages and other non-generation fields
        # This allows engines to receive all generation parameters without hardcoded filtering
        generation_params = {}

        # Fields that are NOT generation parameters and should be excluded
        non_generation_fields = {
            "prompt",
            "messages",
            "model",
            "user",
            "logit_bias",
            "tools",
            "tool_choice",
            "function_call",
            "functions",
        }

        for key, value in data.items():
            if key not in non_generation_fields:
                generation_params[key] = value

        return generation_params

    def _extract_prompt(self, data: dict[str, Any]) -> str | None:
        """
        Extract prompt from request data.

        Priority order:
        1. 'prompt' field (client-formatted single string)
        2. 'messages' field (for models with chat templates)

        Args:
            data: Request data dictionary

        Returns:
            Prompt string or None if neither field is present
        """
        # Check for client-formatted prompt first
        if "prompt" in data:
            prompt = data["prompt"]
            if isinstance(prompt, str):
                return prompt
            else:
                raise ValueError("'prompt' field must be a string")

        # Fallback to messages (for models with chat templates)
        if "messages" in data:
            messages = data["messages"]
            if isinstance(messages, list) and messages:
                # For models with chat templates, return None to let the engine handle messages
                return None
            else:
                raise ValueError("'messages' field must be a non-empty list")

        # Neither field present
        return None

    def _get_model_name(self) -> str:
        """Get model name from path"""
        return os.path.basename(self.model_path)

    def _create_usage_stats(
        self, prompt_tokens: int, completion_tokens: int
    ) -> dict[str, int]:
        """Create usage statistics"""
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
