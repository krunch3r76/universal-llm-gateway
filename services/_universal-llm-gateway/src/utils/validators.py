"""Input validation utilities for API requests"""

import re
from typing import Any

from ..schemas.chat_completion import ChatCompletionRequest, ChatMessage


class ValidationError(Exception):
    """Custom validation error"""

    pass


def validate_model_id(model_id: str) -> str:
    """Validate model ID format"""
    if not model_id:
        raise ValidationError("Model ID cannot be empty")

    if not isinstance(model_id, str):
        raise ValidationError("Model ID must be a string")

    # Check for valid characters (alphanumeric, hyphens, underscores, periods)
    if not re.match(r"^[a-zA-Z0-9_.-]+$", model_id):
        raise ValidationError(
            "Model ID contains invalid characters. Only alphanumeric, hyphens, underscores, and periods allowed"
        )

    if len(model_id) > 100:
        raise ValidationError("Model ID too long (max 100 characters)")

    return model_id.strip()


def validate_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """Validate chat messages"""
    if not messages:
        raise ValidationError("Messages list cannot be empty")

    if not isinstance(messages, list):
        raise ValidationError("Messages must be a list")

    validated_messages = []
    valid_roles = {"system", "user", "assistant"}

    for i, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValidationError(f"Message {i} must be an object")

        # Check required fields
        if "role" not in message:
            raise ValidationError(f"Message {i} missing 'role' field")

        if "content" not in message:
            raise ValidationError(f"Message {i} missing 'content' field")

        role = message["role"]
        content = message["content"]

        # Validate role
        if role not in valid_roles:
            raise ValidationError(
                f"Message {i} has invalid role '{role}'. Must be one of: {valid_roles}"
            )

        # Validate content
        if not isinstance(content, str):
            raise ValidationError(f"Message {i} content must be a string")

        if not content.strip():
            raise ValidationError(f"Message {i} content cannot be empty")

        if len(content) > 50000:  # Reasonable limit
            raise ValidationError(
                f"Message {i} content too long (max 50,000 characters)"
            )

        validated_messages.append(ChatMessage(role=role, content=content))

    # Validate conversation structure
    if validated_messages[0].role == "assistant":
        raise ValidationError("Conversation cannot start with assistant message")

    return validated_messages


def validate_temperature(temperature: float | None) -> float | None:
    """Validate temperature parameter"""
    if temperature is None:
        return None

    if not isinstance(temperature, int | float):
        raise ValidationError("Temperature must be a number")

    if temperature < 0.0 or temperature > 2.0:
        raise ValidationError("Temperature must be between 0.0 and 2.0")

    return float(temperature)


def validate_max_tokens(max_tokens: int | None) -> int | None:
    """Validate max_tokens parameter"""
    if max_tokens is None:
        return None

    if not isinstance(max_tokens, int):
        raise ValidationError("max_tokens must be an integer")

    if max_tokens < 1:
        raise ValidationError("max_tokens must be greater than 0")

    if max_tokens > 32768:  # Reasonable upper limit
        raise ValidationError("max_tokens too large (max 32768)")

    return max_tokens


def validate_top_p(top_p: float | None) -> float | None:
    """Validate top_p parameter"""
    if top_p is None:
        return None

    if not isinstance(top_p, int | float):
        raise ValidationError("top_p must be a number")

    if top_p < 0.0 or top_p > 1.0:
        raise ValidationError("top_p must be between 0.0 and 1.0")

    return float(top_p)


def validate_stop_sequences(
    stop: str | list[str] | None,
) -> list[str] | None:
    """Validate stop sequences"""
    if stop is None:
        return None

    if isinstance(stop, str):
        stop = [stop]

    if not isinstance(stop, list):
        raise ValidationError("stop must be a string or list of strings")

    if len(stop) > 4:  # OpenAI limit
        raise ValidationError("Maximum 4 stop sequences allowed")

    validated_stop = []
    for i, seq in enumerate(stop):
        if not isinstance(seq, str):
            raise ValidationError(f"Stop sequence {i} must be a string")

        if len(seq) > 100:  # Reasonable limit
            raise ValidationError(f"Stop sequence {i} too long (max 100 characters)")

        validated_stop.append(seq)

    return validated_stop


def validate_chat_completion_request(
    request_data: dict[str, Any], query_model: str | None = None
) -> ChatCompletionRequest:
    """Validate complete chat completion request"""
    try:
        # Handle model priority: query parameter > request body
        if query_model:
            request_data["model"] = validate_model_id(query_model)
        elif "model" in request_data and request_data["model"]:
            request_data["model"] = validate_model_id(request_data["model"])
        else:
            raise ValidationError(
                "Model must be specified either in query parameter or request body"
            )

        # Validate using Pydantic
        request = ChatCompletionRequest(**request_data)

        # Additional custom validation
        if request.messages:
            request.messages = validate_chat_messages(
                [msg.dict() for msg in request.messages]
            )

        return request

    except ValidationError:
        raise
    except Exception as e:
        raise ValidationError(f"Invalid request format: {e}")


def sanitize_user_input(text: str, max_length: int = 10000) -> str:
    """Sanitize user input text"""
    if not isinstance(text, str):
        raise ValidationError("Input must be a string")

    # Remove null bytes and control characters
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Trim whitespace
    text = text.strip()

    # Check length
    if len(text) > max_length:
        raise ValidationError(f"Input too long (max {max_length} characters)")

    return text


def validate_api_key(api_key: str | None) -> str | None:
    """Validate API key format"""
    if api_key is None:
        return None

    if not isinstance(api_key, str):
        raise ValidationError("API key must be a string")

    # Remove bearer prefix if present
    if api_key.lower().startswith("bearer "):
        api_key = api_key[7:]

    if not api_key.strip():
        raise ValidationError("API key cannot be empty")

    # Basic format validation (adjust as needed)
    if not re.match(r"^[a-zA-Z0-9_.-]+$", api_key):
        raise ValidationError("API key contains invalid characters")

    return api_key.strip()


def validate_content_type(content_type: str) -> bool:
    """Validate Content-Type header"""
    if not content_type:
        return False

    # Accept application/json with optional charset
    return content_type == "application/json" or content_type.startswith(
        "application/json;"
    )
