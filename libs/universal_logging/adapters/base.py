"""
Base adapter class for framework-specific logging.

This provides a common interface for framework-specific logging adapters.
"""

from typing import Any

from .. import get_logger


class BaseLogger:
    """
    Base class for framework-specific logging adapters.

    Provides common functionality and interface for framework adapters.
    """

    def __init__(self, name: str):
        self.logger = get_logger(name)

    def log_request(
        self,
        method: str,
        path: str,
        status_code: int,
        response_time_ms: float,
        **kwargs,
    ) -> None:
        """Log a request with standard format."""
        self.logger.info(f"{method} {path} - {status_code} - {response_time_ms:.2f}ms")

    def log_framework_request(
        self, request, response, response_time_ms: float, **kwargs
    ) -> None:
        """
        Log framework-specific request/response.

        This method should be overridden by framework-specific adapters.
        """
        raise NotImplementedError("Subclasses must implement log_framework_request")

    def extract_request_data(self, request) -> dict[str, Any]:
        """
        Extract common request data from framework request object.

        This method should be overridden by framework-specific adapters.
        """
        raise NotImplementedError("Subclasses must implement extract_request_data")

    def extract_response_data(self, response) -> dict[str, Any]:
        """
        Extract common response data from framework response object.

        This method should be overridden by framework-specific adapters.
        """
        raise NotImplementedError("Subclasses must implement extract_response_data")
