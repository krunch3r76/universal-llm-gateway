"""Seat claims — agent-seat presence + exclusive claim routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from openapi_mcp.binding import x_mcp
from universal_logging import get_logger

from ..db import cortex_conn
from ..models.seat_claims import (
    SeatClaimRequest,
    SeatClaimResponse,
    SeatClaimsListResponse,
    SeatHeartbeatRequest,
    SeatHeartbeatResponse,
    SeatReleaseRequest,
    SeatReleaseResponse,
)
from ..seat_claim_store import (
    claim_seat,
    heartbeat_seat,
    list_seat_claims,
    release_seat,
)

logger = get_logger("cortex-api.seat_claims")
router = APIRouter(prefix="/seat-claims", tags=["seat-claims"])


@router.post("/claim", response_model=SeatClaimResponse, openapi_extra=x_mcp("seat_claim"))
def seat_claim_route(body: SeatClaimRequest) -> SeatClaimResponse:
    """Claim exclusive hold on a contended key for a seat."""
    conn = cortex_conn()
    try:
        result = claim_seat(
            conn,
            claim_key=body.claim_key,
            seat=body.seat,
            ttl_s=body.ttl_s,
            metadata=body.metadata,
        )
    finally:
        conn.close()
    return SeatClaimResponse.model_validate(result)


@router.post(
    "/heartbeat",
    response_model=SeatHeartbeatResponse,
    openapi_extra=x_mcp("seat_heartbeat"),
)
def seat_heartbeat_route(body: SeatHeartbeatRequest) -> SeatHeartbeatResponse:
    """Refresh liveness for an held claim."""
    conn = cortex_conn()
    try:
        result = heartbeat_seat(conn, holder_id=body.holder_id)
    finally:
        conn.close()
    return SeatHeartbeatResponse.model_validate(result)


@router.post(
    "/release",
    response_model=SeatReleaseResponse,
    openapi_extra=x_mcp("seat_release"),
)
def seat_release_route(body: SeatReleaseRequest) -> SeatReleaseResponse:
    """Release an held claim cleanly."""
    conn = cortex_conn()
    try:
        result = release_seat(conn, holder_id=body.holder_id)
    finally:
        conn.close()
    return SeatReleaseResponse.model_validate(result)


@router.get(
    "",
    response_model=SeatClaimsListResponse,
    openapi_extra=x_mcp("seat_claims_list"),
)
def seat_claims_list_route(
    claim_key: str | None = None,
    seat: str | None = None,
    include_ended: bool = Query(False),
) -> SeatClaimsListResponse:
    """List seat claims; reclaims stale rows first."""
    conn = cortex_conn()
    try:
        result = list_seat_claims(
            conn,
            claim_key=claim_key,
            seat=seat,
            include_ended=include_ended,
        )
    finally:
        conn.close()
    return SeatClaimsListResponse.model_validate(result)


def _seat_claim_impl(
    claim_key: str | None = None,
    seat: str | None = None,
    ttl_s: float | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not claim_key or not seat:
        missing = [
            name
            for name, val in (("claim_key", claim_key), ("seat", seat))
            if not val
        ]
        return {"error": f"{missing[0]} is required"}
    conn = cortex_conn()
    try:
        return claim_seat(
            conn,
            claim_key=claim_key,
            seat=seat,
            ttl_s=ttl_s,
            metadata=metadata,
        )
    finally:
        conn.close()


def _seat_heartbeat_impl(holder_id: str | None = None) -> dict[str, Any]:
    if not holder_id:
        return {"error": "holder_id is required"}
    conn = cortex_conn()
    try:
        return heartbeat_seat(conn, holder_id=holder_id)
    finally:
        conn.close()


def _seat_release_impl(holder_id: str | None = None) -> dict[str, Any]:
    if not holder_id:
        return {"error": "holder_id is required"}
    conn = cortex_conn()
    try:
        return release_seat(conn, holder_id=holder_id)
    finally:
        conn.close()


def _seat_claims_list_impl(
    claim_key: str | None = None,
    seat: str | None = None,
    include_ended: bool = False,
    **_: object,
) -> dict[str, Any]:
    conn = cortex_conn()
    try:
        return list_seat_claims(
            conn,
            claim_key=claim_key,
            seat=seat,
            include_ended=include_ended,
        )
    finally:
        conn.close()
