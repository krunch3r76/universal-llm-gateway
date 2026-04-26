"""Pure passthrough generation params and OpenAI-shaped completion responses."""

from __future__ import annotations

from fastapi import Request

from src.schemas.chat_completion import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatCompletionUsage,
    ChatMessage,
)


def build_generation_params(completion_request: ChatCompletionRequest) -> dict:
    """
    Extract generation parameters from request (pure passthrough).

    INVARIANT: Pure passthrough - ¬defaults, ¬override, ¬validation

    Removes only routing metadata (model, messages, stream) that workers
    don't need. All generation parameters (temperature, max_tokens, etc.)
    pass through unchanged.
    """
    generation_params = completion_request.model_dump(exclude_unset=True)
    for key in ["model", "messages", "prompt", "stream"]:
        generation_params.pop(key, None)
    return generation_params


def build_completion_response(
    completion_result: dict,
    model_id: str,
) -> ChatCompletionResponse:
    """Build ChatCompletionResponse from worker result."""
    content = completion_result.get("content", "")
    finish_reason = completion_result.get("finish_reason", "stop")
    message_args: dict = {"role": "assistant", "content": content}
    if tool_calls := completion_result.get("tool_calls"):
        message_args["tool_calls"] = tool_calls
    response_message = ChatMessage(**message_args)
    choice = ChatCompletionChoice(
        index=0, message=response_message, finish_reason=finish_reason
    )
    usage = ChatCompletionUsage(
        prompt_tokens=completion_result.get("prompt_tokens", 0),
        completion_tokens=completion_result.get("completion_tokens", 0),
        total_tokens=completion_result.get("total_tokens", 0),
    )
    return ChatCompletionResponse(
        model=model_id,
        choices=[choice],
        usage=usage,
        timings=completion_result.get("timings"),
    )


def resolve_gateway_url(request: Request | None) -> str:
    """Resolve gateway URL/identity for request-scoped runtime telemetry."""
    if request is None:
        return "unknown"
    base_url = str(request.base_url).rstrip("/")
    return base_url or "unknown"
