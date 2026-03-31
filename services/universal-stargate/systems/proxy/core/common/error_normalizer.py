"""
Error Normalization Module

Provides centralized error normalization for multiple API formats.

Supported Formats:
- OpenAI: {"error": {"message": "...", "type": "...", "code": "..."}}
- Anthropic: {"error": {"type": "...", "message": "..."}}
- Google AI: {"error": {"code": 503, "message": "...", "status": "UNAVAILABLE"}}
- Custom: {"error": {all fields}}

Usage:
    # OpenAI format (default)
    status, error = ErrorNormalizer.normalize_to_format(exc)

    # Anthropic format
    status, error = ErrorNormalizer.normalize_to_format(exc, format="anthropic")
"""

from typing import Any

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError

from .error_formats import format_anthropic, format_custom, format_google, format_openai
from .error_types import ErrorFormat, determine_error_type, determine_status_code


class ErrorNormalizer:
    """
    Centralized error normalization to multiple API formats.

    This class provides static methods to convert various error types into
    format-compliant error responses. All public methods return either:
    - Tuple[int, dict]: (status_code, error_dict)
    - dict: error_dict (when status code is provided separately)
    """

    @staticmethod
    def normalize_to_format(
        error: Exception | dict | Any,
        format: str | ErrorFormat = ErrorFormat.OPENAI,
        default_status: int = 500,
        operation: str = "unknown",
    ) -> tuple[int, dict]:
        """
        Normalize any error to the specified API format.

        Args:
            error: Error source (exception, dict, HTTP response)
            format: Target error format (openai, anthropic, google, custom)
            default_status: Default HTTP status code if not determinable
            operation: Operation context for error message

        Returns:
            Tuple of (status_code, error_dict) in specified format
        """
        # Convert string to enum if needed
        if isinstance(format, str):
            try:
                format = ErrorFormat(format)
            except ValueError:
                format = ErrorFormat.OPENAI

        # Extract error info (format-agnostic)
        error_info = ErrorNormalizer._extract_error_info(
            error, default_status, operation
        )

        # Format according to target API
        if format == ErrorFormat.OPENAI:
            return format_openai(error_info)
        elif format == ErrorFormat.ANTHROPIC:
            return format_anthropic(error_info)
        elif format == ErrorFormat.GOOGLE:
            return format_google(error_info)
        elif format == ErrorFormat.CUSTOM:
            return format_custom(error_info)
        else:
            return format_openai(error_info)

    @staticmethod
    def normalize_to_openai_format(
        error: Any,
        default_status: int = 500,
        operation: str | None = None,
        gateway_name: str | None = None,
    ) -> tuple[int, dict]:
        """
        Normalize error to OpenAI format (legacy method).

        This method is preserved for backward compatibility with the
        gateway_name parameter.
        New code should use normalize_to_format() instead.
        """
        # Build operation context including gateway_name (legacy parameter)
        operation_context = ""
        if gateway_name and operation:
            operation_context = f"[{gateway_name}] {operation}"
        elif gateway_name:
            operation_context = f"[{gateway_name}]"
        elif operation:
            operation_context = operation

        return ErrorNormalizer.normalize_to_format(
            error,
            format=ErrorFormat.OPENAI,
            default_status=default_status,
            operation=operation_context or "unknown",
        )

    @staticmethod
    def _extract_error_info(
        error: Exception | dict | Any, default_status: int, operation: str
    ) -> dict:
        """
        Extract error information in a format-agnostic way.

        Returns a dict with standard error fields:
        {
            "status_code": int,
            "message": str,
            "error_type": str,
            "code": str,
            "param": Optional[str],
            "model": Optional[str],
            "operation": str
        }
        """
        # Handle httpx.Response (gateway errors)
        if hasattr(error, "status_code") and hasattr(error, "text"):
            return ErrorNormalizer._extract_from_httpx_response(error, operation)

        # Handle FastAPI HTTPException
        if isinstance(error, HTTPException):
            return ErrorNormalizer._extract_from_http_exception(error, operation)

        # Handle Pydantic RequestValidationError
        if isinstance(error, RequestValidationError):
            return ErrorNormalizer._extract_from_validation_error(error, operation)

        # Handle Python exceptions
        if isinstance(error, Exception):
            return ErrorNormalizer._extract_from_exception(
                error, default_status, operation
            )

        # Handle dict (already structured error)
        if isinstance(error, dict):
            return ErrorNormalizer._extract_from_dict(error, default_status, operation)

        # Handle string
        if isinstance(error, str):
            return {
                "status_code": default_status,
                "message": _add_operation_context(error, operation),
                "error_type": "api_error",
                "code": "unknown_error",
                "param": None,
                "model": None,
                "operation": operation,
            }

        # Fallback for unknown error types
        return {
            "status_code": default_status,
            "message": _add_operation_context(str(error), operation),
            "error_type": "api_error",
            "code": "unknown_error",
            "param": None,
            "model": None,
            "operation": operation,
        }

    @staticmethod
    def _extract_from_httpx_response(error: Any, operation: str) -> dict:
        """Extract error info from httpx.Response."""
        status_code = error.status_code
        error_type = determine_error_type(status_code)
        message = f"Gateway error (HTTP {status_code})"
        code = f"http_{status_code}"
        param = None
        model = None

        try:
            body = error.json()
            extracted = _extract_from_body(body, status_code)
            message = extracted.get("message", message)
            code = extracted.get("code", code)
            param = extracted.get("param")
            model = extracted.get("model")
        except Exception:
            text = error.text.strip() if hasattr(error, "text") else str(error)
            message = text or message

        return {
            "status_code": status_code,
            "message": _add_operation_context(message, operation),
            "error_type": error_type,
            "code": code,
            "param": param,
            "model": model,
            "operation": operation,
        }

    @staticmethod
    def _extract_from_http_exception(error: HTTPException, operation: str) -> dict:
        """Extract error info from FastAPI HTTPException."""
        status_code = error.status_code
        error_type = determine_error_type(status_code)
        detail = error.detail
        code = f"http_{status_code}"
        param = None
        model = None

        if isinstance(detail, dict):
            if "error" in detail and isinstance(detail["error"], dict):
                error_obj = detail["error"]
                message = error_obj.get("message") or str(detail)
                code = error_obj.get("code") or code
                param = error_obj.get("param")
                model = error_obj.get("model")
            elif "message" in detail and "type" in detail:
                message = detail.get("message") or str(detail)
                code = detail.get("code") or code
                param = detail.get("param")
                model = detail.get("model")
            else:
                message = (
                    detail.get("message")
                    or detail.get("error")
                    or detail.get("detail")
                    or str(detail)
                )
                code = detail.get("code") or code
        else:
            message = str(detail) if detail else f"HTTP {status_code} error"

        return {
            "status_code": status_code,
            "message": _add_operation_context(message, operation),
            "error_type": error_type,
            "code": code,
            "param": param,
            "model": model,
            "operation": operation,
        }

    @staticmethod
    def _extract_from_validation_error(
        error: RequestValidationError, operation: str
    ) -> dict:
        """Extract error info from Pydantic RequestValidationError."""
        errors = error.errors()
        if not errors:
            message = "Validation failed"
            param = None
        else:
            error_messages = []
            first_param = None

            for err in errors:
                loc = err.get("loc", ())
                msg = err.get("msg", "Invalid value")
                loc_parts = [str(p) for p in loc if p != "body"]
                field = ".".join(loc_parts)

                if not first_param:
                    first_param = field

                if field:
                    error_messages.append(f"{field}: {msg}")
                else:
                    error_messages.append(msg)

            message = "Invalid request: " + "; ".join(error_messages)
            param = first_param

        return {
            "status_code": 400,
            "message": _add_operation_context(message, operation),
            "error_type": "invalid_request_error",
            "code": "validation_failed",
            "param": param,
            "model": None,
            "operation": operation,
        }

    @staticmethod
    def _extract_from_exception(
        error: Exception, default_status: int, operation: str
    ) -> dict:
        """Extract error info from Python exception."""
        status_code = determine_status_code(error, default_status)
        message = str(error) or error.__class__.__name__
        error_type = determine_error_type(status_code)

        return {
            "status_code": status_code,
            "message": _add_operation_context(message, operation),
            "error_type": error_type,
            "code": error.__class__.__name__.lower(),
            "param": None,
            "model": None,
            "operation": operation,
        }

    @staticmethod
    def _extract_from_dict(error: dict, default_status: int, operation: str) -> dict:
        """Extract error info from dict."""
        if "error" in error and isinstance(error["error"], dict):
            error_obj = error["error"]
            message = error_obj.get("message") or str(error)
            error_type = error_obj.get("type") or "api_error"
            code = error_obj.get("code") or "unknown_error"
            param = error_obj.get("param")
            model = error_obj.get("model")
        else:
            message = (
                error.get("message")
                or error.get("error")
                or error.get("detail")
                or str(error)
            )
            error_type = error.get("type") or "api_error"
            code = error.get("code") or "unknown_error"
            param = error.get("param")
            model = error.get("model")

        return {
            "status_code": default_status,
            "message": _add_operation_context(message, operation),
            "error_type": error_type,
            "code": code,
            "param": param,
            "model": model,
            "operation": operation,
        }


def _add_operation_context(message: str, operation: str) -> str:
    """Add operation context to message if meaningful."""
    if operation and operation != "unknown":
        if operation.endswith("]"):
            return f"{operation} {message}"
        return f"{operation}: {message}"
    return message


def _extract_from_body(body: dict, status_code: int) -> dict:
    """Extract error fields from response body dict."""
    result = {
        "message": f"Gateway error (HTTP {status_code})",
        "code": f"http_{status_code}",
        "param": None,
        "model": None,
    }

    if not isinstance(body, dict):
        return result

    # Check for nested detail.error structure
    if "detail" in body:
        detail = body["detail"]
        if isinstance(detail, dict) and "error" in detail:
            error_obj = detail["error"]
            if isinstance(error_obj, dict):
                result["message"] = error_obj.get("message") or result["message"]
                result["code"] = error_obj.get("code") or result["code"]
                result["param"] = error_obj.get("param")
                result["model"] = error_obj.get("model")
                return result
            result["message"] = str(error_obj)
            return result
        if isinstance(detail, dict) and ("message" in detail or "type" in detail):
            result["message"] = detail.get("message") or result["message"]
            result["code"] = detail.get("code") or result["code"]
            result["param"] = detail.get("param")
            result["model"] = detail.get("model")
            return result
        result["message"] = str(detail)
        return result

    # Check for direct error key
    if "error" in body:
        error_obj = body["error"]
        if isinstance(error_obj, dict):
            result["message"] = error_obj.get("message") or result["message"]
            result["code"] = error_obj.get("code") or result["code"]
            result["param"] = error_obj.get("param")
            result["model"] = error_obj.get("model")
            return result
        result["message"] = str(error_obj)
        return result

    # Try common error fields
    result["message"] = (
        body.get("message")
        or body.get("error")
        or body.get("status_message")
        or body.get("detail")
        or result["message"]
    )
    result["code"] = body.get("code") or result["code"]

    return result
