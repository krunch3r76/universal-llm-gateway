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
        status_code=get_http_status(ErrorCode.INVALID_REQUEST),
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
    """Raise error when model genuinely absent from all gateway catalogs."""
    raise HTTPException(
        status_code=get_http_status(ErrorCode.MODEL_NOT_FOUND),
        detail=error_envelope(
            code=ErrorCode.MODEL_NOT_FOUND,
            message=f"Model {model_id} not found in any gateway catalog",
            source="master",
            retryable=False,
            data={"model_id": str(model_id)},
        ),
    )


def raise_no_feasible_gateway_error(
    model_id: str,
    constraint_summary: dict[str, Any],
    *,
    retryable: bool = True,
) -> None:
    """Raise error when model exists in catalogs but all gateways are infeasible.

    When no routing candidate has the model in its catalog (stale federation
    presence only), callers pass ``retryable=False`` so the request fails
    immediately instead of spinning in the capacity retry loop.
    """
    raise HTTPException(
        status_code=get_http_status(ErrorCode.NO_FEASIBLE_GATEWAY),
        detail=error_envelope(
            code=ErrorCode.NO_FEASIBLE_GATEWAY,
            message=f"Model {model_id} exists but no gateway can serve it now",
            source="master",
            retryable=retryable,
            data={"model_id": str(model_id), **constraint_summary},
        ),
    )


def raise_all_gateways_excluded_error(
    model_id: str,
    excluded_gateway_ids: list[str],
    upstream_errors: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Raise non-retryable error when all gateways with the model have been excluded.

    ∀ upstream failures: excluded gateways ⊇ gateways_with_model ⟹ fail immediately.
    Retrying on the same failed gateway wastes the upstream budget without progress.

    When upstream_errors is provided, the original upstream status code is
    preserved so the client sees the real cause (e.g. 429 rate-limit vs
    generic 503 unavailable).
    """
    upstream_codes = set()
    if upstream_errors:
        for err_ctx in upstream_errors.values():
            code = err_ctx.get("upstream_status_code")
            if code is not None:
                upstream_codes.add(int(code))

    # If every upstream failure is 429, report 429 to the client so rate-limit
    # semantics are preserved end-to-end.
    if upstream_codes == {429}:
        http_status = 429
        message = f"Provider rate limit (429) for model {model_id}"
    else:
        http_status = get_http_status(ErrorCode.RESOURCE_UNAVAILABLE)
        message = f"All gateways for model {model_id} have returned upstream errors"

    data: dict[str, Any] = {
        "model_id": model_id,
        "excluded_gateways": excluded_gateway_ids,
        **({"upstream_errors": upstream_errors} if upstream_errors else {}),
    }

    raise HTTPException(
        status_code=http_status,
        detail=error_envelope(
            code=ErrorCode.RESOURCE_UNAVAILABLE,
            message=message,
            source="master",
            retryable=False,
            data=data,
        ),
    )


def raise_inference_banned_error(model_id: str, banned_gateway_ids: list[str]) -> None:
    """Raise non-retryable error when all gateways have session-lifetime inference bans.

    Inference bans are applied when the model cannot run even with exclusive GPU
    and therefore indicates a persistent VRAM mismatch (e.g., after OOM
    recovery failure).
    The ban persists for the Stargate session lifetime and clears on reconnect.
    """
    raise HTTPException(
        status_code=get_http_status(ErrorCode.RESOURCE_UNAVAILABLE),
        detail=error_envelope(
            code=ErrorCode.RESOURCE_UNAVAILABLE,
            message=f"Model {model_id} is inference-banned on all available gateways",
            source="master",
            retryable=False,
            data={
                "model_id": str(model_id),
                "banned_gateway_ids": banned_gateway_ids,
                "reason": "inference_banned",
            },
        ),
    )


def raise_insufficient_resources_error(model_id: str, reason: str) -> None:
    """Non-retryable: model in catalog but hardware cannot load it even after eviction.

    ∀ VRAM/RAM failure where can_fit_with_eviction also fails:
    no idle models can free enough space → permanent hardware constraint.
    """
    raise HTTPException(
        status_code=get_http_status(ErrorCode.RESOURCE_UNAVAILABLE),
        detail=error_envelope(
            code=ErrorCode.RESOURCE_UNAVAILABLE,
            message=f"Model {model_id} cannot be served: insufficient resources",
            source="master",
            retryable=False,
            data={"model_id": str(model_id), "reason": reason},
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
        **({"gateway_url": gateway_url} if gateway_url else {}),
    }

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
