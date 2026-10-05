"""S6 batch 7 — typed routes for register_skill_substrate and view_render."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query
from openapi_mcp.binding import x_mcp

from ..models.substrate_s6_batch7 import RegisterSkillSubstrateRequest

router = APIRouter(tags=["substrate-s6-batch7"])


@router.post(
    "/skills/register-substrate",
    openapi_extra=x_mcp("register_skill_substrate"),
)
def register_skill_substrate_route(
    body: RegisterSkillSubstrateRequest,
) -> dict[str, Any]:
    """Register skill substrate composite (dispatch ``register_skill_substrate`` op)."""
    from ..dispatch_ops.ops_composites import _op_register_skill_substrate

    return _op_register_skill_substrate(**body.model_dump(exclude_unset=True))


@router.get(
    "/views/{document_id}",
    openapi_extra=x_mcp("view_render"),
)
def view_render_route(
    document_id: str,
    asof: Annotated[str | None, Query(alias="asof")] = None,
    mode: Annotated[str, Query()] = "read_asof",
    root_id: Annotated[str | None, Query()] = None,
    view_profile: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
    session_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Render or read a derived view (dispatch ``view_render`` op).

    ``asof`` maps to ``as_of_system`` for ``mode=read_asof`` (bind proposal
    ``GET /views/{id}?asof=``). Other modes remain available for parity with
    dispatch callers that pass explicit ``mode``.
    """
    from ..dispatch_ops.ops_views import _op_view_render

    payload: dict[str, Any] = {
        "document_id": document_id,
        "mode": mode,
    }
    if asof is not None:
        payload["as_of_system"] = asof
    if root_id is not None:
        payload["root_id"] = root_id
    if view_profile is not None:
        payload["view_profile"] = view_profile
    if agent is not None:
        payload["agent"] = agent
    if session_id is not None:
        payload["session_id"] = session_id
    return _op_view_render(**payload)
