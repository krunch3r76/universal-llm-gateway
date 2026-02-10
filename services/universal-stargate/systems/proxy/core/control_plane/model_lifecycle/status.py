"""
Model status types and helpers for gateway orchestration.

Provides enums, dataclasses, and utility functions for model state management.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
from model_id import ModelId
from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

logger = get_logger(__name__)


class ModelLoadingStatus(Enum):
    """Status of model loading operations."""

    LOADED = "loaded"  # Model is confirmed ready for inference
    TIMED_OUT = "timed_out"  # Could not confirm readiness within timeout
    FAILED = "failed"  # Definitive failure (network error, 4xx/5xx, etc.)


class ModelStatus(Enum):
    """Model state on a gateway."""

    NOT_FOUND = "not_found"  # Model not tracked by gateway (not in catalog)
    LOADED = "loaded"  # Ready for inference
    BUSY = "busy"  # Processing a request
    LOADING = "loading"  # Currently loading
    ERROR = "error"  # Failed to load or crashed
    UNKNOWN = "unknown"  # Unrecognized status string


# Mapping from gateway status strings to ModelStatus enum
STATUS_STRING_MAP: dict[str, ModelStatus] = {
    "loaded": ModelStatus.LOADED,
    "busy": ModelStatus.BUSY,
    "loading": ModelStatus.LOADING,
    "error": ModelStatus.ERROR,
    "unloaded": ModelStatus.NOT_FOUND,
    "not_loaded": ModelStatus.NOT_FOUND,
}


@dataclass(frozen=True, slots=True)
class GatewayStatusResult:
    """
    Result of a gateway status check.

    Attributes:
        reachable: True if gateway responded, False if connection failed
        status: Model status (only valid if reachable=True)
        error_message: Error details if status is ERROR
    """

    reachable: bool
    status: ModelStatus = ModelStatus.NOT_FOUND
    error_message: str | None = None

    @property
    def is_loaded(self) -> bool:
        return self.reachable and self.status == ModelStatus.LOADED

    @property
    def is_busy(self) -> bool:
        return self.reachable and self.status == ModelStatus.BUSY

    @property
    def is_loading(self) -> bool:
        return self.reachable and self.status == ModelStatus.LOADING

    @property
    def is_error(self) -> bool:
        return self.reachable and self.status == ModelStatus.ERROR

    @property
    def is_in_catalog(self) -> bool:
        """
        Check if model exists in gateway's catalog (regardless of load state).

        Returns True if model is tracked by gateway (loaded, busy, loading, error, unknown).
        Returns False only if model is NOT_FOUND (not in catalog at all).
        """
        return self.reachable and self.status != ModelStatus.NOT_FOUND


def parse_model_status(status_str: str) -> ModelStatus:
    """
    Parse gateway status string to ModelStatus enum.

    Args:
        status_str: Status string from gateway API (case-insensitive)

    Returns:
        Corresponding ModelStatus enum value, or UNKNOWN if not recognized
    """
    return STATUS_STRING_MAP.get(status_str.lower(), ModelStatus.UNKNOWN)


def extract_error_message(model_info: dict[str, Any]) -> str | None:
    """
    Extract error message from gateway model info dict.

    Handles various error message locations and formats.

    Args:
        model_info: Model info dict from gateway status API

    Returns:
        Error message string, or None if no error message found
    """
    error_message: Any = (
        model_info.get("error_message")
        or model_info.get("error")
        or model_info.get("status_message")
        or model_info.get("message")
    )

    if isinstance(error_message, dict):
        error_message = (
            error_message.get("message")
            or error_message.get("error")
            or str(error_message)
        )

    return str(error_message) if error_message else None


async def get_model_status(
    gateway: GatewayInstance, model_id: str
) -> GatewayStatusResult:
    """
    Get model status from gateway with reachability information.

    Returns a GatewayStatusResult containing:
    - reachable: False if gateway connection failed
    - status: ModelStatus enum value
    - error_message: Error details if status is ERROR
    """
    try:
        client = gateway.client.get_http_client()
        response = await client.get("/api/v1/status/detailed")

        if response.status_code != 200:
            logger.debug(
                f"Status check failed on {gateway.config.name}: {response.status_code}"
            )
            return GatewayStatusResult(reachable=True, status=ModelStatus.UNKNOWN)

        payload = response.json()
        models = payload.get("models", {})

        # Gateway returns canonical IDs (without -hybrid suffix)
        # Normalize for matching
        try:
            normalized_query = ModelId.parse(model_id).normalized
            # Try exact match first
            model_info = models.get(model_id)
            # Fall back to normalized matching if exact fails
            if not model_info:
                for gw_model_id, info in models.items():
                    if ModelId.parse(gw_model_id).normalized == normalized_query:
                        model_info = info
                        break
        except ValueError:
            # Fallback to exact match if parsing fails
            model_info = models.get(model_id)

        if not model_info:
            return GatewayStatusResult(reachable=True, status=ModelStatus.NOT_FOUND)

        status_str = model_info.get("status", "")
        status = parse_model_status(status_str)

        error_message = (
            extract_error_message(model_info) if status == ModelStatus.ERROR else None
        )

        if status != ModelStatus.LOADED:
            logger.debug(
                f"Model {model_id} on {gateway.config.name}: status={status.value}"
            )

        return GatewayStatusResult(
            reachable=True, status=status, error_message=error_message
        )

    except httpx.TransportError as exc:
        logger.debug(f"Gateway {gateway.config.name} unreachable for {model_id}: {exc}")
        return GatewayStatusResult(reachable=False)
    except Exception as exc:
        logger.debug(
            f"Status check failed on {gateway.config.name} for {model_id}: {exc}"
        )
        return GatewayStatusResult(reachable=True, status=ModelStatus.UNKNOWN)
