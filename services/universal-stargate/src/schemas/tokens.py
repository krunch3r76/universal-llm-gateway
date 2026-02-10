"""Token counting and management schemas

⚠️ CRITICAL: When using model_dump() on these schemas:
- Always use exclude_unset=True to avoid adding fields client didn't send
- Use mode="python" not mode="json" to preserve nested dicts/schemas
- See: docs/pydantic-passthrough-rules.md
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageURL(BaseModel):
    """Image URL specification for multi-modal content"""

    url: str = Field(..., description="Image URL (data URI or HTTP(S) URL)")
    detail: Literal["auto", "low", "high"] | None = Field(
        None, description="Image detail level (OpenAI-compatible)"
    )


class ContentPartText(BaseModel):
    """Text content part"""

    type: Literal["text"] = "text"
    text: str = Field(..., description="Text content")


class ContentPartImage(BaseModel):
    """Image content part"""

    type: Literal["image_url"] = "image_url"
    image_url: ImageURL = Field(..., description="Image URL specification")


ContentPart = Annotated[ContentPartText | ContentPartImage, Field(discriminator="type")]


class Message(BaseModel):
    """Chat message with support for text-only and multi-modal content"""

    role: Literal["system", "user", "assistant"] = Field(
        ..., description="Message role"
    )
    content: str | list[ContentPart] = Field(
        ..., description="Message content (string for text-only, list for multi-modal)"
    )


class TokenCountRequest(BaseModel):
    """Request schema for token counting endpoint"""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "description": "Text-only messages",
                    "value": {
                        "messages": [
                            {
                                "role": "system",
                                "content": "You are a helpful assistant.",
                            },
                            {"role": "user", "content": "Hello, how are you?"},
                        ],
                        "model_name": "deepseek-chat-67b-q4km",
                    },
                },
                {
                    "description": "Multi-modal messages (vision model)",
                    "value": {
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": "data:image/png;base64,iVBORw0KG..."
                                        },
                                    },
                                    {"type": "text", "text": "What is in this image?"},
                                ],
                            }
                        ],
                        "model_name": "qwen2-5-vl-7b-instruct-ud-q8-k-xl-8192",
                    },
                },
            ]
        }
    )

    messages: list[Message] | None = Field(
        None,
        description="List of messages to count tokens for (supports multi-modal content)",
    )
    prompt: str | None = Field(None, description="Prompt string to count tokens for")
    model_name: str = Field(..., description="Model name to use for tokenization")

    @model_validator(mode="after")
    def validate_messages_or_prompt(self):
        """Ensure either messages or prompt is provided, but not both"""
        if self.messages is not None and self.prompt is not None:
            raise ValueError("Cannot provide both messages and prompt")
        if self.messages is None and self.prompt is None:
            raise ValueError("Must provide either messages or prompt")
        return self


class TokenCountResponse(BaseModel):
    """Response schema for token counting endpoint"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "token_count": 25,
                "context_limit": 4096,
                "max_generation_tokens": 4071,
            }
        }
    )

    token_count: int = Field(..., description="Number of tokens in the messages")
    context_limit: int = Field(..., description="Maximum context length for the model")
    max_generation_tokens: int = Field(
        ..., description="Maximum tokens available for generation"
    )


class TokenCountError(BaseModel):
    """Error response schema for token counting failures"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "Tokenization failed",
                "details": "Model not loaded or tokenizer unavailable",
            }
        }
    )

    error: str = Field(..., description="Error message")
    details: str | None = Field(None, description="Additional error details")


class TokenMetrics(BaseModel):
    """Token metrics for monitoring"""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "input_tokens": 25,
                "max_tokens_requested": 1000,
                "max_tokens_adjusted": 500,
                "context_limit": 4096,
                "max_tokens_absolute": 4071,
                "safety_buffer": 50,
            }
        }
    )

    input_tokens: int = Field(..., description="Number of input tokens")
    max_tokens_requested: int = Field(
        ..., description="Originally requested max tokens"
    )
    max_tokens_adjusted: int = Field(
        ..., description="Adjusted max tokens after any safety buffer"
    )
    context_limit: int = Field(..., description="Model context limit")
    max_tokens_absolute: int = Field(..., description="Available tokens for generation")
    safety_buffer: int = Field(..., description="Safety buffer applied")
