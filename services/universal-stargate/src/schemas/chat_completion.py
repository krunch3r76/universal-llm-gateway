"""OpenAI Chat Completion API compatible schemas

⚠️ CRITICAL: When using model_dump() on these schemas:
- Always use exclude_unset=True to avoid adding fields client didn't send
- Use mode="python" not mode="json" to preserve nested dicts/schemas
- See: docs/pydantic-passthrough-rules.md
"""

import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatMessage(BaseModel):
    """Chat message in the conversation (supports multimodal content)"""

    model_config = ConfigDict(extra="allow")

    role: str = Field(..., description="Message role: system, user, or assistant")
    content: str | list[dict[str, Any]] = Field(
        ...,
        description="Message content: string for text-only, or list of parts for multimodal (text/image_url)",
    )


class ChatCompletionRequest(BaseModel):
    """Request schema for chat completion endpoint - simplified to avoid default value issues"""

    model_config = ConfigDict(
        extra="allow",  # Allow additional fields not defined in schema
        exclude_none=True,  # Don't serialize None values
        exclude_unset=True,  # Don't serialize unset fields
    )

    model: str | None = Field(
        None, description="Model ID (optional if provided via query parameter)"
    )

    # Support both chat and instruction formats for graceful degradation
    messages: list[ChatMessage] | None = Field(
        None, description="List of conversation messages (for chat models)"
    )
    prompt: str | list[ChatMessage] | None = Field(
        None,
        description="Single prompt string or messages array (for instruction models via degradation)",
    )

    # Token counting bypass for time-critical requests
    skip_token_counting: bool | None = Field(
        None,
        description="Skip token counting for time-critical requests (client responsible for max_tokens)",
    )

    # Let the engine handle all generation parameters - no defaults applied here
    # This prevents the schema from modifying the original request

    def model_post_init(self, __context) -> None:
        """Validate that either messages or prompt is provided"""
        if not self.messages and not self.prompt:
            raise ValueError("Either 'messages' or 'prompt' field must be provided")
        if self.messages and self.prompt:
            raise ValueError("Cannot provide both 'messages' and 'prompt' fields")


class ChatCompletionChoice(BaseModel):
    """Individual choice in chat completion response"""

    index: int = Field(..., description="Choice index")
    message: ChatMessage = Field(..., description="Assistant response message")
    finish_reason: str = Field(..., description="Reason for completion finish")


class ChatCompletionUsage(BaseModel):
    """Token usage information"""

    prompt_tokens: int = Field(..., description="Number of tokens in the prompt")
    completion_tokens: int = Field(
        ..., description="Number of tokens in the completion"
    )
    total_tokens: int = Field(..., description="Total number of tokens")


class ChatCompletionResponse(BaseModel):
    """Chat completion response schema"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "chatcmpl-123456789012",
                "object": "chat.completion",
                "created": 1699000000,
                "model": "deepseek-chat-67b-q4km",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Hello! How can I help you today?",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 8,
                    "completion_tokens": 12,
                    "total_tokens": 20,
                },
            }
        }
    )

    id: str = Field(
        default_factory=lambda: f"chatcmpl-{uuid.uuid4().hex[:12]}",
        description="Unique completion ID",
    )
    object: str = Field(default="chat.completion", description="Object type")
    created: int = Field(
        default_factory=lambda: int(time.time()), description="Creation timestamp"
    )
    model: str = Field(..., description="Model used for completion")
    choices: list[ChatCompletionChoice] = Field(
        ..., description="List of completion choices"
    )
    usage: ChatCompletionUsage = Field(..., description="Token usage information")


# Streaming response schemas (for future implementation)
class ChatCompletionStreamDelta(BaseModel):
    """Delta content for streaming responses (supports multimodal)"""

    model_config = ConfigDict(extra="allow")

    role: str | None = None
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionStreamChoice(BaseModel):
    """Individual choice in streaming response"""

    index: int
    delta: ChatCompletionStreamDelta
    finish_reason: str | None = None


class ChatCompletionStreamResponse(BaseModel):
    """Streaming response chunk"""

    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionStreamChoice]
