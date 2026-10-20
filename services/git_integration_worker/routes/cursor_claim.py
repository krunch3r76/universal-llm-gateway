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
    release,
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


class ReleaseBody(BaseModel):
    """Body for ``POST /claim/{id}/release``.

    ``outcome`` is completed, failed, or withdrawn. Only the agent that holds
    the claim may release it; a repeat of the same outcome is idempotent.
    """

    holder_agent: str
    outcome: Literal["completed", "failed", "withdrawn"]


def _invalid(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"code": code, "message": message}
    )


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
        }
        if body.ttl_s is not None:
            kwargs["ttl_s"] = body.ttl_s
        return claim(**kwargs)
    except WorkKeyHeld as exc:
        return JSONResponse(
            status_code=409,
            content={"code": "WORK_KEY_HELD", **exc.as_dict()},
        )
    except ValueError as exc:
        return _invalid("INVALID_TTL", str(exc))


@router.post("/claim/{claim_id}/release", response_model=None)
def post_claim_release(claim_id: str, body: ReleaseBody) -> Any:
    """Release a claim the caller holds. Idempotent when the outcome repeats."""
    if not body.holder_agent.strip():
        return _invalid("INVALID_CLAIM", "holder_agent is required")
    try:
        return release(claim_id, body.holder_agent.strip(), body.outcome)
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
    return work_key_ledger_projection(
        work_key,
        limit=limit,
        window_hours=window_hours,
    )
