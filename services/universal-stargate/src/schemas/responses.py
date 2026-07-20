"""General API response schemas. Defines the shared pydantic response models — health, metrics, error, validation-error, and generic success payloads — reused across the service's HTTP endpoints."""

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Simple health check response schema for the `/health` endpoint, giving callers a minimal pydantic-validated payload to confirm the service process is up and responding."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "timestamp": 1759217392,
                "version": "1.0.0",
                "uptime_seconds": 96.14069366455078,
            }
        }
    )

    status: str = Field(..., description="Health status: healthy, degraded, unhealthy")
    timestamp: int = Field(..., description="Health check timestamp")
    version: str = Field(..., description="API version")
    uptime_seconds: float = Field(..., description="Server uptime in seconds")


class MetricsResponse(BaseModel):
    """System metrics response schema exposing service-level operational metrics to callers as a pydantic-validated payload, distinct from the minimal `HealthResponse` liveness check."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "timestamp": 1699000000,
                "system": {
                    "platform": "Linux",
                    "cpu_count": 12,
                    "cpu_usage_percent": 25.5,
                },
                "memory": {
                    "total_mb": 32768,
                    "available_mb": 16384,
                    "usage_percent": 50.0,
                },
                "gpu": {
                    "device_count": 1,
                    "total_vram_mb": 32768,
                    "used_vram_mb": 12288,
                },
                "models": {"loaded_count": 1, "enabled_count": 4, "total_count": 4},
            }
        }
    )

    timestamp: int = Field(
        default_factory=lambda: int(time.time()), description="Metrics timestamp"
    )
    system: dict[str, Any] = Field(..., description="System information")
    memory: dict[str, Any] = Field(..., description="Memory usage information")
    gpu: dict[str, Any] | None = Field(None, description="GPU information")
    models: dict[str, Any] = Field(..., description="Model status information")


class ErrorResponse(BaseModel):
    """Generic error response schema used to report a failed request back to the caller as a pydantic-validated payload, shared across endpoints that do not need the more detailed `ValidationErrorResponse` shape."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "message": "Model not found",
                    "type": "not_found_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            }
        }
    )

    error: dict[str, Any] = Field(..., description="Error information")


class ValidationErrorResponse(BaseModel):
    """Response schema for reporting request-validation failures back to the caller, distinct from the generic `ErrorResponse` used for other failure modes, validated and serialized as a pydantic model."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "message": "Validation failed",
                    "type": "validation_error",
                    "param": "messages",
                    "code": "invalid_request_error",
                },
                "detail": "Field 'messages' is required",
            }
        }
    )

    error: dict[str, Any] = Field(..., description="Validation error details")
    detail: str | None = Field(None, description="Additional error details")


class SuccessResponse(BaseModel):
    """Generic success response schema used for endpoints that only need to confirm an operation completed, without the richer payload shapes of the service's other pydantic response schemas."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Operation completed successfully",
                "data": {"processed_items": 5},
            }
        }
    )

    success: bool = Field(True, description="Operation success status")
    message: str = Field(..., description="Success message")
    data: dict[str, Any] | None = Field(None, description="Optional response data")
