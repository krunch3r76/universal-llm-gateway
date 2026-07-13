"""POST /graph/imprint/propose + commit — life imprint write surface."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from universal_logging import get_logger

from ..db import cortex_conn
from ..events_imprint import (
    graph_imprint_commit_received,
    graph_imprint_commit_rejected,
    graph_imprint_committed,
    graph_imprint_proposed,
    graph_imprint_received,
    graph_imprint_rejected,
    graph_imprint_remember_received,
    graph_imprint_remember_rejected,
    graph_imprint_remembered,
)
from ..life_imprint.apply import ImprintCommitError, commit_imprint_proposal
from ..life_imprint.op_plan import build_op_plan, normalize_patch
from ..life_imprint.proposal_store import create_proposal
from ..life_imprint.registry import load_registry
from ..life_imprint.remember import RememberPreviewResult, run_remember
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
    proposal_id: str | None = None
    context: str = Field(default="cortex.life/v1")


class ImprintCommitRequest(BaseModel):
    proposal_id: str


class ImprintCommitResponse(BaseModel):
    proposal_id: str
    applied: list[dict[str, Any]]
    context: str = Field(default="cortex.life/v1")


class ImprintRememberSuccessResponse(BaseModel):
    proposal_id: str
    committed: bool = True
    applied: list[dict[str, Any]]
    normalized_patch: dict[str, Any]
    deduped: bool = False
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
            proposal_id=None,
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

    proposal_id = None
    if not candidates and op_plan:
        proposal_id = create_proposal(
            normalized_patch=normalized,
            op_plan=op_plan,
            rejects=[],
            candidates=[],
        )

    return ImprintProposeResponse(
        normalized_patch=normalized,
        op_plan=op_plan,
        rejects=[],
        candidates=candidates,
        proposal_id=proposal_id,
        context=registry.context_id,
    )


@router.post("/commit", response_model=None)
def imprint_commit(body: ImprintCommitRequest) -> ImprintCommitResponse:
    """Apply a short-lived proposal by id — frozen op_plan, no patch edits."""
    graph_imprint_commit_received(proposal_id=body.proposal_id)
    try:
        result = commit_imprint_proposal(body.proposal_id)
    except ImprintCommitError as exc:
        graph_imprint_commit_rejected(
            proposal_id=body.proposal_id,
            reject_codes=[exc.code],
        )
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "source": exc.source,
                "retryable": exc.retryable,
                "data": exc.data,
            },
        )

    graph_imprint_committed(
        proposal_id=body.proposal_id,
        applied_count=len(result.get("applied") or []),
    )
    return ImprintCommitResponse.model_validate(result)


@router.post("/remember", response_model=None)
def imprint_remember(body: ImprintProposeRequest):
    """Validate like propose; auto-commit when commit-eligible."""
    registry = _registry()
    patch = body.patch
    stmt_count = _statement_count(patch)

    graph_imprint_remember_received(statement_count=stmt_count, context=registry.context_id)

    try:
        result = run_remember(patch, registry=registry)
    except ImprintCommitError as exc:
        graph_imprint_remember_rejected(
            statement_count=stmt_count,
            reject_count=1,
            reject_codes=[exc.code],
            proposal_id=exc.data.get("proposal_id"),
        )
        return JSONResponse(
            status_code=exc.status,
            content={
                "code": exc.code,
                "message": exc.message,
                "source": exc.source,
                "retryable": exc.retryable,
                "data": exc.data,
            },
        )

    if isinstance(result, RememberPreviewResult):
        reject_codes = sorted({r.code for r in result.rejects})
        graph_imprint_remember_rejected(
            statement_count=stmt_count,
            reject_count=len(result.rejects) or len(result.candidates),
            reject_codes=reject_codes,
        )
        return ImprintProposeResponse(
            normalized_patch=result.normalized_patch,
            op_plan=result.op_plan,
            rejects=[
                ShapeRejectModel(
                    statement_idx=r.statement_idx,
                    code=r.code,
                    detail=r.detail,
                )
                for r in result.rejects
            ],
            candidates=result.candidates,
            proposal_id=None,
            context=result.context,
        )

    graph_imprint_remembered(
        proposal_id=result.proposal_id,
        applied_count=len(result.applied),
        deduped=result.deduped,
    )
    return ImprintRememberSuccessResponse(
        proposal_id=result.proposal_id,
        committed=True,
        applied=result.applied,
        normalized_patch=result.normalized_patch,
        deduped=result.deduped,
        context=result.context,
    )
