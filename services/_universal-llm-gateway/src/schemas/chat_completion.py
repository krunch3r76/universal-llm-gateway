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
    """
    Request schema for chat completion endpoint.

    VALIDATION: FastAPI automatically validates all fields using these Pydantic constraints:
    - Only negative number exclusions are enforced (e.g., ge=0 for non-negative values)
    - No upper bound constraints - client parameters are passed through to engines
    - Invalid requests get 422 error responses with detailed field-level error messages
    - Only valid requests reach the endpoint handler with guaranteed data integrity
    """

    model: str | None = Field(
        None, description="Model ID (optional if provided via query parameter)"
    )

    # STRICT: Models accept either messages OR prompt, never both
    # No graceful degradation - client must provide correct format for the model
    messages: list[ChatMessage] | None = Field(
        None,
        description="List of conversation messages (for chat models with input_schema='messages')",
    )
    prompt: str | None = Field(
        None, description="Single prompt string (for models with input_schema='prompt')"
    )

    # VALIDATED CONSTRAINTS: Only negative number exclusions are enforced by FastAPI/Pydantic
    max_tokens: int | None = Field(
        None, description="Maximum number of tokens to generate", ge=0
    )
    temperature: float | None = Field(None, description="Sampling temperature", ge=0.0)
    top_p: float | None = Field(None, description="Nucleus sampling parameter", ge=0.0)
    min_p: float | None = Field(
        None, description="Minimum p sampling parameter", ge=0.0
    )
    typical_p: float | None = Field(
        None, description="Typical p sampling parameter", ge=0.0
    )
    top_k: int | None = Field(None, description="Top-k sampling parameter", ge=0)
    n: int | None = Field(None, description="Number of completions to generate", ge=0)
    stream: bool | None = Field(None, description="Whether to stream the response")
    stop: str | list[str] | None = Field(None, description="Stop sequences")
    presence_penalty: float | None = Field(None, description="Presence penalty")
    frequency_penalty: float | None = Field(None, description="Frequency penalty")
    repeat_penalty: float | None = Field(None, description="Repetition penalty", ge=0.0)
    encoder_repetition_penalty: float | None = Field(
        None, description="Encoder repetition penalty", ge=0.0
    )
    no_repeat_ngram_size: int | None = Field(
        None, description="No repeat n-gram size", ge=0
    )
    length_penalty: float | None = Field(None, description="Length penalty")
    diversity_penalty: float | None = Field(
        None, description="Diversity penalty", ge=0.0
    )
    epsilon_cutoff: float | None = Field(None, description="Epsilon cutoff", ge=0.0)
    eta_cutoff: float | None = Field(None, description="Eta cutoff", ge=0.0)
    logit_bias: dict[str, float] | None = Field(None, description="Logit bias")
    seed: int | None = Field(None, description="Random seed for generation")
    tfs_z: float | None = Field(
        None, description="Tail-free sampling parameter", ge=0.0
    )
    mirostat_mode: int | None = Field(None, description="Mirostat sampling mode", ge=0)
    mirostat_tau: float | None = Field(
        None, description="Mirostat target entropy", ge=0.0
    )
    mirostat_eta: float | None = Field(
        None, description="Mirostat learning rate", ge=0.0
    )
    user: str | None = Field(None, description="User identifier")

    def model_post_init(self, __context) -> None:
        """Validate that either messages or prompt is provided"""
        if not self.messages and not self.prompt:
            raise ValueError("Either 'messages' or 'prompt' field must be provided")
        if self.messages and self.prompt:
            raise ValueError("Cannot provide both 'messages' and 'prompt' fields")

    class Config:
        extra = "allow"  # Allow unknown fields to pass through to engines


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
