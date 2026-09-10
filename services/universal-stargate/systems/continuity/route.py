"""Stargate ``/api/v1/continuity/*`` routes (Phase 0 tape-read · Phase 4b checkpoint)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

from systems.proxy.dependencies import get_auth_dependency

from continuity_tape.messages import ContinuityMessagesEnvelope

from .models import CheckpointAccepted, CheckpointRequest, TapeReadRequest
from .tape_read import fetch_tape_envelope

logger = get_logger(__name__)
router = APIRouter(prefix="/continuity", tags=["continuity"])

_PIPELINE_ID = "continuity-checkpoint-v1"


def _iso_utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


@router.post(
    "/tape-read",
    response_model=ContinuityMessagesEnvelope,
    responses={
        403: {"description": "Not a root continuity lane"},
        503: {"description": "agent-bus unreachable"},
    },
)
async def continuity_tape_read(
    body: TapeReadRequest,
    current_user: dict[str, Any] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Door 1 sync relay — agent-bus tape render wrapped as ``ContinuityMessagesEnvelope``."""
    caller_agent = str(current_user.get("agent") or current_user.get("sub") or "stargate")
    payload, status = await fetch_tape_envelope(
        body.thread,
        scope=body.scope,
        transcript_id=body.transcript_id,
        prior_cells=body.prior_cells,
        include_extras=body.include_extras,
        tools=body.tools,
        budget_bytes=body.budget_bytes,
        harvest=body.harvest,
        caller_agent=caller_agent,
        door="sync",
    )
    return JSONResponse(content=payload, status_code=status)


@router.post(
    "/checkpoint",
    response_model=CheckpointAccepted,
    status_code=202,
    responses={
        422: {"description": "Validation or surface not landed"},
        503: {"description": "Pipeline dispatch unavailable"},
    },
)
async def continuity_checkpoint(
    body: CheckpointRequest,
    current_user: dict[str, Any] = Depends(get_auth_dependency),
) -> JSONResponse:
    """Admit continuity-checkpoint-v1 async pipeline for a root lane CHECKPOINT."""
    if body.surface == "claude_ai":
        return _error_response(
            422,
            "checkpoint.surface_not_landed",
            "claude_ai checkpoint leg blocked until Phase 3 harvest lands",
        )

    caller_agent = body.from_agent or str(
        current_user.get("agent") or current_user.get("sub") or "stargate"
    )
    thread = body.thread

    tip_after = 0
    try:
        from agent_bus_store.db import get_thread_turn_count

        tip_after = get_thread_turn_count(thread)
    except Exception:  # noqa: BLE001
        pass

    pipeline_options: dict[str, Any] = {
        "thread": thread,
        "surface": body.surface,
        "from_agent": caller_agent,
        "pre_consolidate": body.pre_consolidate,
        "tools": body.tools,
        "execution_context": "continuity-checkpoint",
    }
    if body.transcript_id is not None:
        pipeline_options["transcript_id"] = body.transcript_id
    if body.jsonl_path is not None:
        pipeline_options["jsonl_path"] = body.jsonl_path
    if body.chat_url is not None:
        pipeline_options["chat_url"] = body.chat_url
    if body.residue is not None:
        pipeline_options["residue"] = body.residue
    if body.pipeline_options:
        pipeline_options.update(body.pipeline_options)

    from systems.proxy.routers.api.pipelines_dispatch import DispatchRequest

    dispatch = DispatchRequest(
        model=_PIPELINE_ID,
        messages=[
            {
                "role": "user",
                "content": f"checkpoint {thread} surface={body.surface}",
            }
        ],
        pipeline_options=pipeline_options,
        dispatch_thread_id=thread,
        caller_agent=caller_agent,
        output_contract="inline",
    )

    async with make_async_client(DEFAULT_STARGATE_URL, timeout=15.0) as client:
        resp = await client.post(
            "/api/v1/pipelines/dispatch",
            json=dispatch.model_dump(exclude_none=True),
        )

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = {"error": {"code": f"http_{resp.status_code}", "message": resp.text[:500]}}
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(content=detail, status_code=resp.status_code)
        return _error_response(
            resp.status_code,
            str(detail.get("code") or f"http_{resp.status_code}"),
            str(detail.get("message") or detail),
        )

    data = resp.json()
    execution_id = str(data.get("execution_id") or "")
    started_at = str(data.get("started_at") or _iso_utc_now())
    accepted = CheckpointAccepted(
        execution_id=execution_id,
        pipeline=_PIPELINE_ID,
        thread=thread,
        started_at=started_at,
        poll_hint={
            "tool": "agent_bus_read",
            "arguments_json": {
                "op": "wait",
                "thread": thread,
                "after_turn": tip_after,
                "completion": "first_reply_from",
                "from_agent": caller_agent,
            },
        },
    )
    return JSONResponse(content=accepted.model_dump(), status_code=202)


__all__ = ["router"]
