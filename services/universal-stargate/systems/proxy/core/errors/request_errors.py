"""
Request processing error builders.

Factory methods for general request validation and processing errors.
"""

from typing import Any

from fastapi import HTTPException


class RequestErrorBuilder:
    """Factory for general request processing errors."""

    @staticmethod
    def model_not_specified() -> HTTPException:
        """Model parameter is required but missing."""
        return HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Model must be specified",
                    "type": "invalid_request_error",
                    "code": "model_required",
                    "param": "model",
                }
            },
        )

    @staticmethod
    def invalid_request(message: str, param: str | None = None) -> HTTPException:
        """Generic invalid request error."""
        detail: dict[str, Any] = {
            "error": {
                "message": message,
                "type": "invalid_request_error",
                "code": "invalid_request",
            }
        }
        if param:
            detail["error"]["param"] = param

        return HTTPException(status_code=400, detail=detail)

    @staticmethod
    def internal_error(message: str, operation: str | None = None) -> HTTPException:
        """Internal server error with operation context."""
        error_detail: dict[str, Any] = {
            "message": f"Internal server error: {message}",
            "type": "internal_error",
            "code": "internal_server_error",
        }
        if operation:
            error_detail["operation"] = operation

        return HTTPException(status_code=500, detail={"error": error_detail})

    @staticmethod
    def request_timeout(message: str, param: str | None = None) -> HTTPException:
        """Request timeout error."""
        detail: dict[str, Any] = {
            "error": {
                "message": message,
                "type": "timeout_error",
                "code": "request_timeout",
            }
        }
        if param:
            detail["error"]["param"] = param

        return HTTPException(status_code=408, detail=detail)
