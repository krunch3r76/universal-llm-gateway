"""
Token management error builders.

Factory methods for token counting and context limit related errors.
"""

from fastapi import HTTPException

from src.schemas.tokens import TokenMetrics


class TokenErrorBuilder:
    """Factory for token management related errors."""

    @staticmethod
    def service_unavailable(model: str, details: str | None = None) -> HTTPException:
        """
        Token counting service is unavailable or failed.

        Returns HTTP 503 - Service Unavailable
        Client should retry after a delay.
        """
        message = (
            f"Token counting failed for model {model}. "
            f"The model may not be loaded or available."
        )
        if details:
            message += f" Details: {details}"

        return HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": message,
                    "type": "model_loading_error",
                    "code": "token_counting_failed",
                    "model": model,
                }
            },
        )

    @staticmethod
    def context_length_exceeded(
        metrics: TokenMetrics, model: str, content_type: str = "messages"
    ) -> HTTPException:
        """
        Input exceeds the model's context window.

        Returns HTTP 400 - Bad Request
        Client must reduce input size.
        """
        return HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"Input too long: {metrics.input_tokens} tokens exceeds "
                        f"context limit of {metrics.context_limit} for model {model}"
                    ),
                    "type": "invalid_request_error",
                    "code": "context_length_exceeded",
                    "param": content_type,
                    "model": model,
                    "input_tokens": metrics.input_tokens,
                    "context_limit": metrics.context_limit,
                    "excess_tokens": metrics.input_tokens - metrics.context_limit,
                }
            },
        )

    @staticmethod
    def insufficient_generation_space(
        metrics: TokenMetrics, model: str, safety_buffer: int = 0
    ) -> HTTPException:
        """
        Input fits but leaves no room for model to generate output.

        Returns HTTP 400 - Bad Request
        Client must reduce input size or increase context limit.
        """
        remaining = metrics.context_limit - metrics.input_tokens

        return HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"No generation space available: "
                        f"Input uses {metrics.input_tokens} of "
                        f"{metrics.context_limit} tokens, leaving insufficient room "
                        f"for generation in model {model}"
                    ),
                    "type": "invalid_request_error",
                    "code": "insufficient_generation_space",
                    "model": model,
                    "input_tokens": metrics.input_tokens,
                    "context_limit": metrics.context_limit,
                    "remaining_tokens": remaining,
                    "safety_buffer": safety_buffer,
                    "suggestion": (
                        "Reduce input size or request a model "
                        "with larger context window"
                    ),
                }
            },
        )

    @classmethod
    def context_length_exceeded_simple(
        cls, input_tokens: int, context_limit: int, model_id: str
    ) -> HTTPException:
        """Context length exceeded error without full TokenMetrics."""
        return HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"Input ({input_tokens} tokens) exceeds "
                        f"context limit ({context_limit})"
                    ),
                    "type": "context_length_exceeded",
                    "code": "context_length_exceeded",
                    "param": "messages",
                    "model": model_id,
                }
            },
        )

    @classmethod
    def insufficient_generation_space_simple(
        cls, input_tokens: int, context_limit: int, safety_buffer: int, model_id: str
    ) -> HTTPException:
        """Insufficient generation space error without full TokenMetrics."""
        return HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": (
                        f"No generation space available "
                        f"(input={input_tokens}, limit={context_limit}, "
                        f"buffer={safety_buffer})"
                    ),
                    "type": "insufficient_tokens",
                    "code": "insufficient_generation_space",
                    "param": "max_tokens",
                    "model": model_id,
                }
            },
        )
