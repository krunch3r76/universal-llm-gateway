"""
Model management error builders.

Factory methods for model loading, routing, and gateway operation errors.
"""

from fastapi import HTTPException


class ModelErrorBuilder:
    """Factory for model management and loading errors."""

    @staticmethod
    def model_not_found(
        model_name: str, available_models: list | None = None
    ) -> HTTPException:
        """Model is not available in the gateway."""
        message = f"Model '{model_name}' not found"
        if available_models:
            message += f". Available models: {', '.join(available_models)}"

        return HTTPException(
            status_code=404,
            detail={
                "error": {
                    "message": message,
                    "type": "model_error",
                    "code": "model_not_found",
                    "model": model_name,
                    "available_models": available_models or [],
                }
            },
        )

    @staticmethod
    def model_loading_failed(
        model_name: str, reason: str | None = None, status_code: int = 503
    ) -> HTTPException:
        """Model failed to load - definitive failure."""
        message = f"Failed to load model '{model_name}'"
        if reason:
            message += f": {reason}"

        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "message": message,
                    "type": "model_error",
                    "code": "model_loading_failed",
                    "model": model_name,
                    "reason": reason,
                }
            },
        )

    @staticmethod
    def model_loading_timeout(model_name: str, timeout_seconds: int) -> HTTPException:
        """Model loading timed out - gateway timeout, not internal error."""
        return HTTPException(
            status_code=504,
            detail={
                "error": {
                    "message": (
                        f"Model '{model_name}' loading timed out after {timeout_seconds} seconds. "
                        f"Large models may take several minutes to load."
                    ),
                    "type": "gateway_timeout",
                    "code": "model_loading_timeout",
                    "model": model_name,
                    "timeout_seconds": timeout_seconds,
                    "suggestion": "Please try again shortly. Large models (30B+) may take 5-10 minutes to load initially.",
                }
            },
        )

    @staticmethod
    def model_token_counting_timeout(
        model_name: str, timeout_seconds: int
    ) -> HTTPException:
        """Token counting for model timed out."""
        return HTTPException(
            status_code=504,
            detail={
                "error": {
                    "message": f"Token counting for model '{model_name}' timed out after {timeout_seconds} seconds",
                    "type": "model_error",
                    "code": "model_token_counting_timeout",
                    "model": model_name,
                    "timeout_seconds": timeout_seconds,
                    "suggestion": "Model may still be loading. Try again in a moment.",
                }
            },
        )

    @staticmethod
    def models_all_busy(busy_models: list | None = None) -> HTTPException:
        """All models are currently busy."""
        message = "All models are currently busy processing other requests"
        if busy_models:
            message += f" (busy models: {', '.join(busy_models)})"
        message += ". Please try again later."

        return HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": message,
                    "type": "capacity_error",
                    "code": "models_all_busy",
                    "busy_models": busy_models or [],
                    "suggestion": "Wait a moment and retry your request",
                }
            },
        )

    @staticmethod
    def gateway_unavailable(
        status_code: int, details: str | None = None
    ) -> HTTPException:
        """Gateway is unavailable or returned an error."""
        message = f"Gateway unavailable (HTTP {status_code})"
        if details:
            message += f": {details}"

        return HTTPException(
            status_code=502,
            detail={
                "error": {
                    "message": message,
                    "type": "gateway_error",
                    "code": "gateway_unavailable",
                    "gateway_status": status_code,
                    "details": details,
                }
            },
        )

    @staticmethod
    def gateway_operation_failed(
        model_name: str,
        operation: str,
        reason: str | None = None,
        status_code: int = 503,
    ) -> HTTPException:
        """
        Generic gateway operation failure (inference, token counting, etc.).

        Use this for any gateway operation that fails.
        """
        message = f"{operation.capitalize()} failed for model '{model_name}'"
        if reason:
            message += f": {reason}"

        return HTTPException(
            status_code=status_code,
            detail={
                "error": {
                    "message": message,
                    "type": "gateway_error",
                    "code": f"{operation.replace(' ', '_')}_failed",
                    "model": model_name,
                    "operation": operation,
                    "reason": reason,
                }
            },
        )

    @staticmethod
    def model_oom_error(
        model_name: str, error_details: str | None = None
    ) -> HTTPException:
        """
        Model failed to load due to GPU/CPU memory exhaustion.

        Returns 507 Insufficient Storage to indicate server resource issue.
        This is a definitive failure - retrying will not help.
        """
        message = (
            f"Model '{model_name}' failed to load due to insufficient GPU/CPU memory (OOM). "
            f"The server does not have enough resources to load this model."
        )
        if error_details:
            # Clean up the error details (remove OOM: prefix if present)
            clean_details = error_details.replace("OOM:", "").strip()
            message += f" Details: {clean_details}"

        return HTTPException(
            status_code=507,  # Insufficient Storage
            detail={
                "error": {
                    "message": message,
                    "type": "resource_error",
                    "code": "model_oom",
                    "model": model_name,
                    "error_details": error_details,
                    "retryable": False,
                    "suggestion": (
                        "This is a server resource issue. Please contact the administrator "
                        "to free up GPU/CPU memory or use a smaller model."
                    ),
                }
            },
        )

    @staticmethod
    def model_resource_error(
        model_name: str, error_details: str | None = None
    ) -> HTTPException:
        """
        Model failed to load due to resource constraints.

        Returns 507 Insufficient Storage for resource errors.
        This is a definitive failure - retrying will not help.
        """
        message = f"Model '{model_name}' failed to load due to resource constraints"
        if error_details:
            clean_details = error_details.replace("RESOURCE:", "").strip()
            message += f": {clean_details}"

        return HTTPException(
            status_code=507,
            detail={
                "error": {
                    "message": message,
                    "type": "resource_error",
                    "code": "model_resource_constraint",
                    "model": model_name,
                    "error_details": error_details,
                    "retryable": False,
                }
            },
        )
