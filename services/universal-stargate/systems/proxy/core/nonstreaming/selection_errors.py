"""
Error handling for gateway selection.

Centralizes HTTPException creation with error_envelope compliance.
"""

from typing import Any

from fastapi import HTTPException
from universal_protocol import ErrorCode, error_envelope, get_http_status


def raise_no_gateways_error() -> None:
    """Raise error when no federated gateways available."""
    raise HTTPException(
        status_code=get_http_status(ErrorCode.GATEWAY_DISCONNECTED),
        detail=error_envelope(
            code=ErrorCode.GATEWAY_DISCONNECTED,
            message="No federated gateways available",
            source="master",
            retryable=True,
            data={},
        ),
    )


def raise_configuration_error(mode: str) -> None:
    """Raise error when routing mechanism not available."""
    raise HTTPException(
        status_code=500,
        detail=error_envelope(
            code=ErrorCode.INVALID_REQUEST,
            message=(
                "No routing mechanism available "
                "(router-only mode requires federated_manager)"
            ),
            source="master",
            retryable=False,
            data={"mode": mode},
        ),
    )


def raise_gateway_capacity_error(gateway_name: str) -> None:
    """Raise error when gateway at capacity."""
    raise HTTPException(
        status_code=get_http_status(ErrorCode.GATEWAY_AT_CAPACITY),
        detail=error_envelope(
            code=ErrorCode.GATEWAY_AT_CAPACITY,
            message=f"Gateway {gateway_name} at capacity",
            source="master",
            retryable=True,
            data={"gateway": gateway_name},
        ),
    )


def raise_capacity_error(
    model_id: str,
    capacity_details: dict[str, Any],
) -> None:
    """Raise error when model at capacity on all gateways."""
    raise HTTPException(
        status_code=get_http_status(ErrorCode.STICKY_CAPACITY),
        detail=error_envelope(
            code=ErrorCode.STICKY_CAPACITY,
            message=f"Sticky model {model_id} at capacity on all gateways",
            source="master",
            retryable=True,
            data=capacity_details,
        ),
    )


def raise_model_unavailable_error(model_id: str) -> None:
    """Raise error when model not available on any gateway."""
    raise HTTPException(
        status_code=get_http_status(ErrorCode.MODEL_NOT_FOUND),
        detail=error_envelope(
            code=ErrorCode.MODEL_NOT_FOUND,
            message=f"No federated gateway available for model {model_id}",
            source="master",
            retryable=False,
            data={"model_id": str(model_id)},
        ),
    )


def raise_eviction_failed_error(
    model_id: str,
    gateway_name: str,
    gateway_url: str | None = None,
) -> None:
    """Raise error when eviction fails."""
    error_data = {
        "model_id": str(model_id),
        "gateway": gateway_name,
    }
    if gateway_url:
        error_data["gateway_url"] = gateway_url

    raise HTTPException(
        status_code=get_http_status(ErrorCode.EVICTION_FAILED),
        detail=error_envelope(
            code=ErrorCode.EVICTION_FAILED,
            message=f"Eviction failed on {gateway_name}, cannot load {model_id}",
            source="master",
            retryable=True,
            data=error_data,
        ),
    )
