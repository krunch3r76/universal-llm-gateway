"""Seat claim dispatch ops."""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

from ..routes.seat_claims import (
    _seat_claim_impl,
    _seat_claims_list_impl,
    _seat_heartbeat_impl,
    _seat_release_impl,
)
from ._shared import record

logger = get_logger("cortex-api.dispatch_ops.seat_claims")


def _op_seat_claim(
    claim_key: str | None = None,
    seat: str | None = None,
    ttl_s: float | None = None,
    metadata: dict[str, Any] | None = None,
    **_: object,
) -> dict[str, Any]:
    result = _seat_claim_impl(
        claim_key=claim_key,
        seat=seat,
        ttl_s=ttl_s,
        metadata=metadata,
    )
    if "error" not in result:
        logger.info(
            "seat_claim: key=%s seat=%s granted=%s",
            claim_key,
            seat,
            result.get("granted"),
        )
        record(
            "mcp.cortex.seat_claim",
            claim_key=claim_key,
            seat=seat,
            granted=result.get("granted"),
        )
    return result


def _op_seat_heartbeat(holder_id: str | None = None, **_: object) -> dict[str, Any]:
    return _seat_heartbeat_impl(holder_id=holder_id)


def _op_seat_release(holder_id: str | None = None, **_: object) -> dict[str, Any]:
    result = _seat_release_impl(holder_id=holder_id)
    if result.get("released"):
        record("mcp.cortex.seat_release", holder_id=holder_id)
    return result


def _op_seat_claims_list(
    claim_key: str | None = None,
    seat: str | None = None,
    include_ended: bool = False,
    **_: object,
) -> dict[str, Any]:
    return _seat_claims_list_impl(
        claim_key=claim_key,
        seat=seat,
        include_ended=include_ended,
    )
