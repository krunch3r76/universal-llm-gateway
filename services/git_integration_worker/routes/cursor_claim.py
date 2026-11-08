"""House claim, release, and occupancy routes beside ``/dispatch``.

Mounted on the same ``/api/v1/cursor`` prefix as the cursor-sdk router. The
ledger claim module owns the transaction; this module only validates the D4
work-key grammar and maps ``WorkKeyHeld`` / ``ClaimError`` onto HTTP.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.git_integration_worker.cursor_sdk_work_claim import (
    ClaimError,
    WorkKeyHeld,
    claim,
    registry,
    release,
    update,
)
from services.git_integration_worker.cursor_sdk_work_key_gate import (
    validate_work_key_scheme,
)

router = APIRouter(prefix="/api/v1/cursor", tags=["cursor-claim"])


class ClaimBody(BaseModel):
    """Body for ``POST /claim``. ``holder_agent`` is the calling seat.

    ``ttl_s`` defaults in the claim module (120 minutes). ``request_turn`` is
    an optional bus watermark stored on the row, not a gate input.
    """

    work_key: str
    holder_agent: str
    thread_id: str
    ttl_s: int | None = None
    request_turn: int | None = None
    seat_name: str | None = None
    address: dict[str, Any] | None = None
    work_status: str | None = None


class ClaimUpdateBody(BaseModel):
    """Body for ``POST /claim/{id}/update`` — metadata patch or terminal release."""

    holder_agent: str
    caller_dispatch_id: str | None = None
    work_status: str | None = None
    address_patch: dict[str, Any] | None = None
    note: str | None = None


class ApiRoleDispatchBody(BaseModel):
    """Persist API-role team_dispatch economics onto the work-key ledger."""

    execution_id: str
    work_key: str
    resolved_model: str
    contract: str | None = None
    usage: dict[str, Any] | None = None
    terminal_status: Literal["completed", "failed"] = "completed"


class RegistryTerminalBody(BaseModel):
    """Close a frontier registry projection row (api_role / cdp)."""

    dispatch_id: str
    terminal_status: Literal["completed", "failed", "cancelled"]
    reason: str = ""


class RegistryHeartbeatBody(BaseModel):
    """Bump ``last_heartbeat_at`` on a live CDP frontier registry row."""

    dispatch_id: str


class RegistryRecordBody(BaseModel):
    """Best-effort auto-record for team_dispatch generate/to_thread admits."""

    dispatch_id: str
    thread_id: str
    caller_agent: str | None = None
    seat_name: str | None = None
    address: dict[str, Any] | None = None
    work_key: str | None = None
    resolved_model: str | None = None
    contract: str | None = None
    max_wall_s: int | None = None


class ReleaseBody(BaseModel):
    """Body for ``POST /claim/{id}/release``.

    ``outcome`` is completed, failed, or withdrawn. Only the agent that holds
    the claim may release it; a repeat of the same outcome is idempotent.
    """

    holder_agent: str
    caller_dispatch_id: str | None = None
    outcome: Literal["completed", "failed", "withdrawn"]


def _invalid(code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=422, content={"code": code, "message": message})


@router.post("/claim", response_model=None)
def post_claim(body: ClaimBody) -> Any:
    """Claim a work_key for one house holder, or 409 when another holder has it.

    Renews the same claim id when the caller polls again before expiry.
    """
    if not validate_work_key_scheme(body.work_key):
        return _invalid("INVALID_WORK_KEY", "work_key failed D4 grammar")
    if not body.holder_agent.strip() or not body.thread_id.strip():
        return _invalid("INVALID_CLAIM", "holder_agent and thread_id are required")
    try:
        kwargs: dict[str, Any] = {
            "work_key": body.work_key,
            "holder_agent": body.holder_agent.strip(),
            "thread_id": body.thread_id.strip(),
            "request_turn": body.request_turn,
            "seat_name": body.seat_name,
            "address": body.address,
            "work_status": body.work_status,
        }
        if body.ttl_s is not None:
            kwargs["ttl_s"] = body.ttl_s
        return claim(**kwargs)
    except ClaimError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc)},
        )
    except WorkKeyHeld as exc:
        return JSONResponse(
            status_code=409,
            content={"code": "WORK_KEY_HELD", **exc.as_dict()},
        )
    except ValueError as exc:
        return _invalid("INVALID_TTL", str(exc))


@router.post("/claim/{claim_id}/update", response_model=None)
def post_claim_update(claim_id: str, body: ClaimUpdateBody) -> Any:
    """Update house-claim metadata; ``landed`` / ``abandoned`` release the row."""
    if not body.holder_agent.strip():
        return _invalid("INVALID_CLAIM", "holder_agent is required")
    try:
        return update(
            claim_id,
            body.holder_agent.strip(),
            work_status=body.work_status,
            address_patch=body.address_patch,
            note=body.note,
            caller_dispatch_id=body.caller_dispatch_id,
        )
    except ClaimError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc)},
        )


@router.post("/registry/record", response_model=None)
def post_registry_record(body: RegistryRecordBody) -> Any:
    """Stamp or upsert one dispatch row for the work registry (best-effort)."""
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        record_dispatch_registry_best_effort,
        registry_next_hint,
    )

    if not body.dispatch_id.strip() or not body.thread_id.strip():
        return _invalid("INVALID_RECORD", "dispatch_id and thread_id are required")
    row_id = record_dispatch_registry_best_effort(
        dispatch_id=body.dispatch_id.strip(),
        thread_id=body.thread_id.strip(),
        caller_agent=body.caller_agent,
        seat_name=body.seat_name,
        address=body.address,
        work_key=body.work_key,
        resolved_model=body.resolved_model,
        contract=body.contract,
        max_wall_s=body.max_wall_s,
    )
    if row_id is None:
        return {"ok": False, "registry_row_id": None}
    return {
        "ok": True,
        "registry_row_id": row_id,
        "_next": registry_next_hint(row_id=row_id),
    }


@router.get("/registry", response_model=None)
def get_registry(
    state: str = Query("live"),
    window_hours: int = Query(24, ge=1, le=168),
    work_key_prefix: str | None = Query(None),
) -> Any:
    """Live work registry (claims + acting dispatches), ≤5120 UTF-8 bytes."""
    try:
        return registry(
            state=state,
            window_hours=window_hours,
            work_key_prefix=work_key_prefix,
        )
    except ClaimError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc)},
        )


@router.post("/claim/{claim_id}/release", response_model=None)
def post_claim_release(claim_id: str, body: ReleaseBody) -> Any:
    """Release a claim the caller holds. Idempotent when the outcome repeats."""
    if not body.holder_agent.strip():
        return _invalid("INVALID_CLAIM", "holder_agent is required")
    try:
        return release(
            claim_id,
            body.holder_agent.strip(),
            body.outcome,
            caller_dispatch_id=body.caller_dispatch_id,
        )
    except ClaimError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": str(exc)},
        )


@router.get("/occupancy", response_model=None)
def get_occupancy(
    work_key: str = Query(..., min_length=1),
) -> Any:
    """Occupancy projection for one work_key. Not a progress report."""
    from services.git_integration_worker.cursor_dispatch_read import (
        occupancy_projection,
    )

    if not validate_work_key_scheme(work_key):
        return _invalid("INVALID_WORK_KEY", "work_key failed D4 grammar")
    return occupancy_projection(work_key)


@router.post("/api-role-dispatch", response_model=None)
def post_api_role_dispatch(body: ApiRoleDispatchBody) -> Any:
    """Record one API-role dispatch row for ``team_dispatch(op=ledger)``."""
    if not validate_work_key_scheme(body.work_key):
        return _invalid("INVALID_WORK_KEY", "work_key failed D4 grammar")
    if not body.execution_id.strip():
        return _invalid("INVALID_EXECUTION", "execution_id is required")
    from services.git_integration_worker.api_role_dispatch_ledger import (
        record_api_role_dispatch,
    )

    execution_id = body.execution_id.strip()
    dispatch_id = record_api_role_dispatch(
        execution_id=execution_id,
        work_key=body.work_key.strip(),
        resolved_model=body.resolved_model.strip(),
        contract=body.contract,
        usage=body.usage,
        terminal_status=body.terminal_status,
    )
    return {"ok": True, "dispatch_id": dispatch_id or f"api-{execution_id}"}


@router.post("/registry/terminal", response_model=None)
def post_registry_terminal(body: RegistryTerminalBody) -> Any:
    """Mark a frontier registry row terminal (api_role / cdp admits)."""
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        close_frontier_registry_row,
    )

    if not body.dispatch_id.strip():
        return _invalid("INVALID_TERMINAL", "dispatch_id is required")
    outcome = close_frontier_registry_row(
        body.dispatch_id.strip(),
        body.terminal_status,
        body.reason or "",
    )
    return {"ok": True, "marked": outcome.get("marked", False)}


@router.post("/registry/heartbeat", response_model=None)
def post_registry_heartbeat(body: RegistryHeartbeatBody) -> Any:
    """Refresh CDP frontier registry liveness (never 404)."""
    from services.git_integration_worker.cursor_sdk_work_registry_admit import (
        bump_frontier_registry_heartbeat,
    )

    if not body.dispatch_id.strip():
        return _invalid("INVALID_HEARTBEAT", "dispatch_id is required")
    outcome = bump_frontier_registry_heartbeat(body.dispatch_id.strip())
    return {"ok": True, "bumped": outcome.get("bumped", False)}


@router.get("/work-key-ledger", response_model=None)
def get_work_key_ledger(
    work_key: str = Query(..., min_length=1, max_length=256),
    limit: int = Query(5, ge=1, le=10),
    window_hours: int = Query(24, ge=1, le=168),
) -> Any:
    """Read-only work-key dispatch ledger (compact JSON, ≤5120 UTF-8 bytes)."""
    from services.git_integration_worker.cursor_dispatch_read import (
        work_key_ledger_projection,
    )

    if not validate_work_key_scheme(work_key):
        return _invalid("INVALID_WORK_KEY", "work_key failed D4 grammar")
    if len(work_key.encode("utf-8")) > 256:
        return _invalid("INVALID_WORK_KEY", "work_key exceeds 256 UTF-8 bytes")
    return work_key_ledger_projection(
        work_key,
        limit=limit,
        window_hours=window_hours,
    )
