"""Stargate-side team_dispatch intake validators (mirrors MCP ``_frontier_intake``)."""

from __future__ import annotations

from typing import Any

from job_vocab import GENERATE_ADMITTED_JOBS, TO_THREAD_ADMITTED_JOBS

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
    session: str | None = None,
    model: str | None = None,
) -> None:
    """Mirror MCP predicates 3/4/5 for Stargate HTTP admit."""
    reject_retired_packet_kind(packet_kind, request_id=request_id)
    from job_grammar import resolve_session

    session_reason = resolve_session(session)
    if session_reason == "session_unknown":
        raise FrontierEndpointError(
            request_id=request_id,
            field="session",
            reason=f"session {session!r} is not an admitted session",
            status_code=422,
            code="session_unknown",
            details={
                "event": "dispatch.job.refused",
                "reason": "session_unknown",
                "registry_ref": "job_vocab:unresolved",
            },
        )
    if op not in ("generate", "to_thread"):
        return
    wire = (contract or "").strip().lower()
    admitted = TO_THREAD_ADMITTED_JOBS if op == "to_thread" else GENERATE_ADMITTED_JOBS
    if not wire:
        raise FrontierEndpointError(
            request_id=request_id,
            field="job",
            reason=(
                f"job '(omitted)' is not admitted for op={op!r}; "
                f"must be one of: {', '.join(sorted(admitted))}"
            ),
            status_code=422,
            code="job_missing",
            details={
                "event": "dispatch.job.refused",
                "reason": "job_missing",
                "registry_ref": "job_vocab:unresolved",
            },
        )
    from job_grammar import resolve_job_token

    parsed = resolve_job_token((contract or "").strip())
    if parsed.reason == "job_unknown":
        raise FrontierEndpointError(
            request_id=request_id,
            field="job",
            reason=f"job {parsed.job!r} is not a registry job",
            status_code=422,
            code="job_unknown",
            details={
                "event": "dispatch.job.refused",
                "reason": "job_unknown",
                "registry_ref": parsed.registry_ref,
            },
        )
    if wire == "check-review" and not (model or "").strip():
        raise FrontierEndpointError(
            request_id=request_id,
            field="model",
            reason=(
                "job=check-review requires an explicit model on the "
                "check-review allowlist"
            ),
            status_code=422,
            code="handle_forbidden",
            details={
                "event": "dispatch.job.refused",
                "reason": "handle_forbidden",
                "registry_ref": "job_vocab:check-review",
            },
        )
    if wire not in admitted:
        raise FrontierEndpointError(
            request_id=request_id,
            field="job",
            reason=(
                f"job {wire!r} is not admitted for op={op!r}; "
                f"must be one of: {', '.join(sorted(admitted))}"
            ),
            status_code=422,
            code="job_not_admitted",
        )
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
            reason="stop_after is forbidden with job='freeform'",
            status_code=422,
            code="none_with_stop_after",
        )


def reject_dispatch_id_off_sdk(
    *,
    request_id: str,
    dispatch_id: str | None,
    sdk_admission: bool,
) -> None:
    """Raise 422 ``dispatch_id_sdk_only`` when a reserved id cannot be honored.

    Only the cursor-sdk branch forwards ``dispatch_id`` to GIW. Dropping it
    silently on any other branch would leave the caller's stop-slot claim
    pointing at an id that never becomes a ledger row.
    """
    if not dispatch_id or sdk_admission:
        return
    raise FrontierEndpointError(
        request_id=request_id,
        field="dispatch_id",
        reason=(
            "dispatch_id is only honored on seat='cursor-sdk' generate; "
            "other branches mint their own ids"
        ),
        status_code=422,
        code="dispatch_id_sdk_only",
    )


def intake_error_to_response(exc: FrontierEndpointError) -> dict[str, Any]:
    return exc.to_dict()
