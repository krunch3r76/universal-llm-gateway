"""Stargate-side team_dispatch intake validators (mirrors MCP ``_frontier_intake``)."""

from __future__ import annotations

from typing import Any

from .service import FrontierEndpointError


def reject_retired_packet_kind(
    packet_kind: str | None,
    *,
    request_id: str,
) -> None:
    if packet_kind is None:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="packet_kind",
        reason="packet_kind is retired; use contract='conductor' for conductor spawn",
        status_code=422,
        code="packet_kind_retired",
    )


def reject_unsupported_packet_inputs(
    *,
    request_id: str,
    op: str,
    contract: str | None,
    packet_path: str | None,
    source_ref: str | None,
    packet_kind: str | None = None,
    stop_after: str | None = None,
) -> None:
    """Mirror MCP predicates 3/4/5 for Stargate HTTP admit."""
    reject_retired_packet_kind(packet_kind, request_id=request_id)
    if op not in ("generate", "to_thread"):
        return
    wire = (contract or "").strip().lower()
    if wire in {"none", "pure-mechanical"} and source_ref is not None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=(
                f"source_ref is forbidden for contract={wire!r}; "
                "pick a materializer contract"
            ),
            status_code=422,
            code=f"{wire}_with_source_ref",
        )
    if wire == "none" and stop_after:
        raise FrontierEndpointError(
            request_id=request_id,
            field="stop_after",
            reason="stop_after is forbidden with contract='none'",
            status_code=422,
            code="none_with_stop_after",
        )


def intake_error_to_response(exc: FrontierEndpointError) -> dict[str, Any]:
    return exc.to_dict()
