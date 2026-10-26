"""HTTP routes for house action journal append and joined house read."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from ...db.house_action_journal import (
    AppendHouseActionError,
    append_house_action,
    list_joined_houses,
)
from ...events.house_action_appended import emit_house_action_appended
from . import router as threads_router

router = threads_router


class HouseActionAppendBody(BaseModel):
    root_thread_id: str
    kind: str
    actor: str
    action_why: str = Field(min_length=1)


def _refuse(exc: AppendHouseActionError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": exc.code},
    )


@router.post("/house-actions")
async def append_house_action_route(body: HouseActionAppendBody) -> dict[str, Any]:
    """Append one house action row when validation passes."""
    try:
        row = append_house_action(**body.model_dump())
    except AppendHouseActionError as exc:
        raise _refuse(exc) from exc
    emit_house_action_appended(
        root_thread_id=row["root_thread_id"],
        kind=row["kind"],
        actor=row["actor"],
        action_why=row["action_why"],
        journal_id=int(row["id"]),
    )
    return {"appended": True, "row": row}


@router.get("/house-actions")
async def list_house_actions_route() -> dict[str, Any]:
    """Return joined active liaison house rows."""
    return {"houses": list_joined_houses()}
