"""
Authentication and authorization error builders.

Factory methods for API key and permission related errors.
"""

from fastapi import HTTPException


class AuthErrorBuilder:
    """Factory for authentication/authorization errors."""

    @staticmethod
    def api_key_required() -> HTTPException:
        """API key is required but not provided."""
        return HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": (
                        "API key required. Provide your API key using an Authorization "
                        "header, X-API-Key header, or api_key query parameter."
                    ),
                    "type": "invalid_request_error",
                    "code": "api_key_required",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @staticmethod
    def invalid_api_key() -> HTTPException:
        """API key is provided but invalid."""
        return HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Invalid API key provided.",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
