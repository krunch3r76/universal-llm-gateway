"""
Error format converters for different API styles.

Provides formatters for OpenAI, Anthropic, Google, and custom error formats.
"""


def format_openai(error_info: dict) -> tuple[int, dict]:
    """
    Format error info as OpenAI API error.

    OpenAI Format:
    {
      "error": {
        "message": "...",
        "type": "...",
        "code": "...",
        "param": null
      }
    }
    """
    error_dict = {
        "error": {
            "message": error_info["message"],
            "type": error_info["error_type"],
            "code": error_info["code"],
        }
    }

    # Add optional fields if present
    if error_info.get("param"):
        error_dict["error"]["param"] = error_info["param"]
    if error_info.get("model"):
        error_dict["error"]["model"] = error_info["model"]

    return error_info["status_code"], error_dict


def format_anthropic(error_info: dict) -> tuple[int, dict]:
    """
    Format error info as Anthropic API error.

    Anthropic Format:
    {
      "error": {
        "type": "...",
        "message": "..."
      }
    }

    Anthropic error types:
    - invalid_request_error
    - authentication_error
    - permission_error
    - not_found_error
    - request_too_large
    - rate_limit_error
    - api_error
    - overloaded_error
    """
    # Map generic error type to Anthropic-specific type
    type_mapping = {
        "invalid_request_error": "invalid_request_error",
        "authentication_error": "authentication_error",
        "permission_error": "permission_error",
        "api_error": "api_error",
        "service_unavailable": "overloaded_error",
        "timeout": "overloaded_error",
        "rate_limit_exceeded": "rate_limit_error",
    }

    anthropic_type = type_mapping.get(error_info["error_type"], "api_error")

    error_dict = {"error": {"type": anthropic_type, "message": error_info["message"]}}

    return error_info["status_code"], error_dict


def format_google(error_info: dict) -> tuple[int, dict]:
    """
    Format error info as Google AI API error.

    Google AI Format:
    {
      "error": {
        "code": 503,
        "message": "...",
        "status": "UNAVAILABLE"
      }
    }

    Google status codes:
    - INVALID_ARGUMENT (400)
    - UNAUTHENTICATED (401)
    - PERMISSION_DENIED (403)
    - NOT_FOUND (404)
    - RESOURCE_EXHAUSTED (429)
    - INTERNAL (500)
    - UNAVAILABLE (503)
    - DEADLINE_EXCEEDED (504)
    """
    # Map HTTP status to Google status string
    status_mapping = {
        400: "INVALID_ARGUMENT",
        401: "UNAUTHENTICATED",
        403: "PERMISSION_DENIED",
        404: "NOT_FOUND",
        429: "RESOURCE_EXHAUSTED",
        500: "INTERNAL",
        503: "UNAVAILABLE",
        504: "DEADLINE_EXCEEDED",
    }

    google_status = status_mapping.get(error_info["status_code"], "INTERNAL")

    error_dict = {
        "error": {
            "code": error_info["status_code"],
            "message": error_info["message"],
            "status": google_status,
        }
    }

    return error_info["status_code"], error_dict


def format_custom(error_info: dict) -> tuple[int, dict]:
    """
    Format error info as custom/generic error.

    Custom Format (includes all available fields):
    {
      "error": {
        "status_code": 503,
        "message": "...",
        "type": "...",
        "code": "...",
        "operation": "...",
        "param": null,
        "model": null
      }
    }
    """
    error_dict = {
        "error": {
            "status_code": error_info["status_code"],
            "message": error_info["message"],
            "type": error_info["error_type"],
            "code": error_info["code"],
            "operation": error_info["operation"],
        }
    }

    # Add optional fields
    if error_info.get("param"):
        error_dict["error"]["param"] = error_info["param"]
    if error_info.get("model"):
        error_dict["error"]["model"] = error_info["model"]

    return error_info["status_code"], error_dict
