"""
Structured error hierarchy for gateway operations.

Provides typed exceptions for different gateway failure modes, enabling
proper error handling without scattered logging.
"""

from typing import Any


class GatewayError(Exception):
    """Base exception for all gateway errors."""

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        context: dict[str, Any] | None = None,
    ):
        """
        Initialize gateway error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the gateway that encountered the error
            context: Additional context information
        """
        super().__init__(message)
        self.message = message
        self.gateway_url = gateway_url
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert error to dictionary for structured logging."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "gateway_url": self.gateway_url,
            "context": self.context,
        }


class ConnectivityError(GatewayError):
    """
    Gateway is unreachable at the network level.

    This indicates network connectivity issues, DNS resolution failures,
    or the gateway service not listening on the expected port.
    """

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        underlying_error: Exception | None = None,
    ):
        """
        Initialize connectivity error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the unreachable gateway
            underlying_error: Original exception that caused this error
        """
        context = {}
        if underlying_error:
            context["underlying_error"] = str(underlying_error)
            context["underlying_type"] = type(underlying_error).__name__

        super().__init__(message, gateway_url, context)
        self.underlying_error = underlying_error


class HealthError(GatewayError):
    """
    Gateway is reachable but service is degraded or unhealthy.

    This indicates the gateway responded but is not in a healthy state
    to process requests (e.g., overloaded, internal errors).
    """

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        health_status: str | None = None,
        health_data: dict[str, Any] | None = None,
    ):
        """
        Initialize health error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the unhealthy gateway
            health_status: Gateway's reported health status
            health_data: Full health check response data
        """
        context = {
            "health_status": health_status,
            "health_data": health_data or {},
        }
        super().__init__(message, gateway_url, context)
        self.health_status = health_status
        self.health_data = health_data


class GatewayTimeoutError(GatewayError):
    """
    Gateway request timed out.

    This indicates the gateway took too long to respond, which could be
    due to overload, slow model operations, or network latency.
    """

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        timeout_seconds: float | None = None,
        operation: str | None = None,
    ):
        """
        Initialize timeout error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the gateway that timed out
            timeout_seconds: Timeout duration that was exceeded
            operation: Operation that timed out (e.g., "health_check", "load_model")
        """
        context = {
            "timeout_seconds": timeout_seconds,
            "operation": operation,
        }
        super().__init__(message, gateway_url, context)
        self.timeout_seconds = timeout_seconds
        self.operation = operation


class ModelLoadError(GatewayError):
    """
    Failed to load a model on the gateway.

    This indicates the gateway accepted the load request but failed to
    load the model (e.g., model not found, insufficient resources).
    """

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        model_id: str | None = None,
        error_details: dict[str, Any] | None = None,
    ):
        """
        Initialize model load error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the gateway
            model_id: Model identifier that failed to load
            error_details: Additional error details from gateway
        """
        context = {
            "model_id": model_id,
            "error_details": error_details or {},
        }
        super().__init__(message, gateway_url, context)
        self.model_id = model_id
        self.error_details = error_details


class ModelUnloadError(GatewayError):
    """
    Failed to unload a model from the gateway.

    This indicates the gateway could not unload the model (e.g., model
    is busy with active inference, model not loaded).
    """

    def __init__(
        self,
        message: str,
        gateway_url: str | None = None,
        model_id: str | None = None,
        reason: str | None = None,
    ):
        """
        Initialize model unload error.

        Args:
            message: Human-readable error message
            gateway_url: URL of the gateway
            model_id: Model identifier that failed to unload
            reason: Reason for unload failure
        """
        context = {
            "model_id": model_id,
            "reason": reason,
        }
        super().__init__(message, gateway_url, context)
        self.model_id = model_id
        self.reason = reason


class NoHealthyGatewaysError(GatewayError):
    """
    No healthy gateways available for request routing.

    This indicates all configured gateways are either unreachable or
    unhealthy, preventing request processing.
    """

    def __init__(
        self,
        message: str = "No healthy gateways available",
        total_gateways: int | None = None,
        gateway_states: dict[str, str] | None = None,
    ):
        """
        Initialize no healthy gateways error.

        Args:
            message: Human-readable error message
            total_gateways: Total number of configured gateways
            gateway_states: Current state of each gateway
        """
        context = {
            "total_gateways": total_gateways,
            "gateway_states": gateway_states or {},
        }
        super().__init__(message, None, context)
        self.total_gateways = total_gateways
        self.gateway_states = gateway_states
