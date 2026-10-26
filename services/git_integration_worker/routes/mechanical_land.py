"""Mechanical-land run birth and read routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from universal_logging import get_logger

from services.git_integration_worker.mechanical_land.store import (
    MechanicalLandStore,
    MechanicalLandStoreError,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/mechanical-land", tags=["mechanical-land"])


class MechanicalLandPlanBody(BaseModel):
    """POST body — pipeline ``plan`` phase output plus caller metadata."""

    admitted: bool = True
    kind: str = "implement"
    source_ref: str = ""
    policy: dict[str, Any] = Field(default_factory=dict)
    policy_sha256: str = ""
    dispatch_thread_id: str = ""
    caller_agent: str = "mechanical-land"
    idempotency_key: str | None = None
    transition: dict[str, Any] | None = None
    op: str | None = None


def _store() -> MechanicalLandStore:
    return MechanicalLandStore()


@router.post("/runs")
async def create_mechanical_land_run(
    body: MechanicalLandPlanBody, response: Response
) -> dict[str, Any]:
    """Birth a mechanical-land run and admit the first implement leg."""
    plan = body.model_dump()
    store = _store()
    try:
        result = store.birth_from_plan(plan, idempotency_key=body.idempotency_key)
    except MechanicalLandStoreError as exc:
        code = str(exc)
        if code == "lane_thread_unavailable":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason_code": "lane_thread_unavailable"},
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason_code": code},
        ) from exc

    payload: dict[str, Any] = {
        "run_id": result.run_id,
        "lane_thread_id": result.lane_thread_id,
        "dispatch_id": result.dispatch_id,
        "execution_id": result.execution_id,
        "status": result.status,
        "journal": f"cortex://notes/system/runs/mechanical-land/{result.run_id}.md",
    }
    if not result.admit_ok:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=payload,
        )
    response.status_code = result.http_status
    return payload


@router.get("/runs/{run_id}")
async def get_mechanical_land_run(run_id: str) -> dict[str, Any]:
    """Return folded ``RunState`` and the tail of journal events."""
    store = _store()
    snapshot = store.get_run(run_id, event_limit=20)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"run_id": run_id},
        )
    return snapshot
