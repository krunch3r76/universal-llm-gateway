"""POST /graph/imprint/propose — zero-write op planning for cortex.life/v1 patches."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from universal_logging import get_logger

from ..db import cortex_conn
from ..events_imprint import (
    graph_imprint_proposed,
    graph_imprint_received,
    graph_imprint_rejected,
)
from ..life_imprint.op_plan import build_op_plan, normalize_patch
from ..life_imprint.registry import load_registry
from ..life_imprint.shape_check import shape_check_patch

router = APIRouter(prefix="/graph/imprint", tags=["graph-imprint"])
logger = get_logger("cortex-api.graph-imprint")

_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


class ImprintProposeRequest(BaseModel):
    patch: dict[str, Any]
    dry_run_context: dict[str, Any] | None = None


class ShapeRejectModel(BaseModel):
    statement_idx: int
    code: str
    detail: str


class ImprintProposeResponse(BaseModel):
    normalized_patch: dict[str, Any]
    op_plan: list[dict[str, Any]]
    rejects: list[ShapeRejectModel]
    candidates: list[dict[str, Any]]
    context: str = Field(default="cortex.life/v1")


def _statement_count(patch: dict[str, Any]) -> int:
    graph = patch.get("@graph")
    if isinstance(graph, list):
        return len(graph)
    return 1


@router.post("/propose", response_model=ImprintProposeResponse)
def imprint_propose(body: ImprintProposeRequest) -> ImprintProposeResponse:
    """Accept hand-authored cortex.life/v1 patch; return op plan without writes."""
    registry = _registry()
    patch = body.patch
    stmt_count = _statement_count(patch)

    graph_imprint_received(statement_count=stmt_count, context=registry.context_id)

    normalized = normalize_patch(patch, registry)
    rejects = shape_check_patch(patch, registry)

    if rejects:
        codes = sorted({r.code for r in rejects})
        graph_imprint_rejected(
            statement_count=stmt_count,
            reject_count=len(rejects),
            reject_codes=codes,
        )
        return ImprintProposeResponse(
            normalized_patch=normalized,
            op_plan=[],
            rejects=[
                ShapeRejectModel(
                    statement_idx=r.statement_idx,
                    code=r.code,
                    detail=r.detail,
                )
                for r in rejects
            ],
            candidates=[],
            context=registry.context_id,
        )

    conn = cortex_conn()
    try:
        op_plan, candidates = build_op_plan(patch, registry, conn)
    finally:
        conn.close()

    graph_imprint_proposed(
        statement_count=stmt_count,
        op_plan_count=len(op_plan),
        candidate_count=len(candidates),
    )

    return ImprintProposeResponse(
        normalized_patch=normalized,
        op_plan=op_plan,
        rejects=[],
        candidates=candidates,
        context=registry.context_id,
    )
