"""
FastAPI adapter for universal logging.

Provides FastAPI-specific logging functionality.
"""

from typing import Any

from fastapi import Request, Response

from .base import BaseLogger


class FastAPILogger(BaseLogger):
    """
    FastAPI-specific logging adapter.

    Provides FastAPI-specific request/response logging.
    """

    def log_framework_request(
        self, request: Request, response: Response, response_time_ms: float, **kwargs
    ) -> None:
        """Log FastAPI request/response with framework-specific data."""
        request_data = self.extract_request_data(request)
        response_data = self.extract_response_data(response)

        # Combine all data
        extra_data = {
            **request_data,
            **response_data,
            "response_time_ms": response_time_ms,
            **kwargs,
        }

        # Log using the standard request logging method
        self.log_request(
            method=request_data["method"],
            path=request_data["path"],
            status_code=response_data["status_code"],
            response_time_ms=response_time_ms,
            **extra_data,
        )

    def extract_request_data(self, request: Request) -> dict[str, Any]:
        """Extract common request data from FastAPI Request object."""
        return {
            "method": request.method,
            "path": request.url.path,
            "query_params": str(request.query_params),
            "client_ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent"),
            "content_type": request.headers.get("content-type"),
            "content_length": request.headers.get("content-length"),
        }

    def extract_response_data(self, response: Response) -> dict[str, Any]:
        """Extract common response data from FastAPI Response object."""
        return {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type"),
            "content_length": response.headers.get("content-length"),
        }
