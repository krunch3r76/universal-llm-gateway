"""POST /api/v1/life/intent/propose + commit — thin handlers over libs/life_intent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from life_intent.commit import CommitReject, apply_commit
from life_intent.events import (
    life_intent_committed,
    life_intent_proposed,
    life_intent_received,
    life_intent_rejected,
)
from life_intent.intent_check import check_intent
from life_intent.proposal_store import create_proposal
from life_intent.registry import load_registry
from life_intent.work_order import render_work_order
from pydantic import BaseModel, Field
from universal_logging import get_logger

life_intent_router = APIRouter(prefix="/api/v1/life/intent", tags=["life-intent"])
logger = get_logger(__name__)

_REGISTRY = None


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


def _publish_event(event: Any) -> None:
    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(event)
    except Exception:
        pass


class IntentBody(BaseModel):
    verb: str
    subject: str
    detail: str
    refs: list[str] = Field(default_factory=list)
    urgency: str | None = "normal"


class ProposeRequest(BaseModel):
    intent: IntentBody


class RejectModel(BaseModel):
    code: str
    detail: str


class ProposeResponse(BaseModel):
    proposal_id: str | None
    normalized_intent: dict[str, Any] | None
    work_order: str | None
    questions: list[str]
    rejects: list[RejectModel]
    context: str = Field(default="cortex.life-intent/v1")


class CommitRequest(BaseModel):
    proposal_id: str
    reply_thread: str | None = None


class CommitResponse(BaseModel):
    committed: bool
    entity_id: str | None = None
    dispatch_ref: str | None = None
    reply_thread: str | None = None
    rejects: list[RejectModel] = Field(default_factory=list)
    context: str = Field(default="cortex.life-intent/v1")


def _default_ref_resolver(ref: str) -> bool:
    if "missing:" in ref:
        return False
    if ref.startswith(("cortex://", "agent-bus:", "todo:", "decision:")):
        return True
    if ref.startswith("workspaces://"):
        return True
    return False


@life_intent_router.post("/propose", response_model=ProposeResponse)
async def intent_propose(body: ProposeRequest) -> ProposeResponse:
    registry = _registry()
    intent_dict = body.intent.model_dump()
    verb = str(intent_dict.get("verb") or "")

    ev_received = life_intent_received(
        verb=verb or "unknown",
        ref_count=len(intent_dict.get("refs") or []),
        context=registry.context_id,
    )
    _publish_event(ev_received)

    result = check_intent(intent_dict, registry, ref_resolver=_default_ref_resolver)

    if result.rejects:
        codes = sorted({r.code for r in result.rejects})
        ev = life_intent_rejected(
            verb=verb or None,
            reject_count=len(result.rejects),
            reject_codes=codes,
        )
        _publish_event(ev)
        return ProposeResponse(
            proposal_id=None,
            normalized_intent=result.normalized_intent,
            work_order=None,
            questions=[],
            rejects=[RejectModel(code=r.code, detail=r.detail) for r in result.rejects],
        )

    if result.questions:
        return ProposeResponse(
            proposal_id=None,
            normalized_intent=result.normalized_intent,
            work_order=None,
            questions=list(result.questions),
            rejects=[],
        )

    assert result.normalized_intent is not None
    work_order = render_work_order(result.normalized_intent, registry)
    proposal_id = create_proposal(
        normalized_intent=result.normalized_intent,
        work_order=work_order,
        verb=result.normalized_intent["verb"],
        lane=registry.verbs[result.normalized_intent["verb"]].lane,
    )

    ev = life_intent_proposed(
        verb=result.normalized_intent["verb"],
        question_count=0,
        proposal_id=proposal_id,
    )
    _publish_event(ev)

    return ProposeResponse(
        proposal_id=proposal_id,
        normalized_intent=result.normalized_intent,
        work_order=work_order,
        questions=[],
        rejects=[],
    )


@life_intent_router.post("/commit", response_model=None)
async def intent_commit(body: CommitRequest) -> CommitResponse | JSONResponse:
    from life_intent.proposal_store import get_proposal

    row = get_proposal(body.proposal_id)
    verb = row.verb if row else "unknown"
    outcome = await apply_commit(body.proposal_id, reply_thread=body.reply_thread)

    if isinstance(outcome, CommitReject):
        ev = life_intent_rejected(
            verb=None,
            reject_count=1,
            reject_codes=[outcome.code],
        )
        _publish_event(ev)
        return CommitResponse(
            committed=False,
            rejects=[RejectModel(code=outcome.code, detail=outcome.detail)],
        )

    ev = life_intent_committed(
        verb=verb,
        proposal_id=outcome.proposal_id,
        entity_id=outcome.entity_id,
        dispatch_ref=outcome.dispatch_ref,
    )
    _publish_event(ev)

    return CommitResponse(
        committed=True,
        entity_id=outcome.entity_id,
        dispatch_ref=outcome.dispatch_ref,
        reply_thread=outcome.reply_thread,
    )
