"""POST /graph/recall/matter + continuity — life-recall G1 read surface."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from universal_logging import get_logger

from ..db import cortex_conn
from ..events_recall import (
    graph_recall_burst_not_covered,
    graph_recall_card_served,
    graph_recall_escalated_to_delegate,
    graph_recall_resolver_miss,
)
from ..recall_card import build_recall_card
from ..recall_models import RecallCard, RecallNull, RecallRequest
from ..terminal_facts import is_terminal_facts_hub

router = APIRouter(prefix="/graph/recall", tags=["graph-recall"])
logger = get_logger("cortex-api.graph-recall")


def _emit_recall_events(
    card: RecallCard,
    *,
    mode: Literal["matter", "continuity"],
    q_present: bool,
    seed_count: int,
) -> None:
    null_values = [n.value for n in card.nulls]
    graph_recall_card_served(
        mode=mode,
        resolved_count=len(card.resolved),
        nulls=null_values,
    )
    if RecallNull.resolver_miss in card.nulls:
        graph_recall_resolver_miss(
            mode=mode,
            q_present=q_present,
            seed_count=seed_count,
        )
    if RecallNull.vocab_not_covered in card.nulls:
        hub_ids = [r.entity_id for r in card.resolved if is_terminal_facts_hub(r.entity_id)]
        graph_recall_burst_not_covered(mode=mode, hub_ids=hub_ids)
    if card.next_advisory is not None:
        graph_recall_escalated_to_delegate(
            mode=mode,
            reason=card.next_advisory.reason,
        )


def _handle_recall(
    body: RecallRequest,
    *,
    mode: Literal["matter", "continuity"],
) -> RecallCard:
    conn = cortex_conn()
    try:
        card = build_recall_card(
            conn,
            mode=mode,
            q=body.q,
            seeds=body.seeds,
        )
    finally:
        conn.close()
    _emit_recall_events(
        card,
        mode=mode,
        q_present=bool(body.q and body.q.strip()),
        seed_count=len(body.seeds or []),
    )
    return card


@router.post("/matter", response_model=RecallCard)
def recall_matter(body: RecallRequest) -> RecallCard:
    """Matter-mode recall — hub orientation with burst plug-in and activation."""
    try:
        return _handle_recall(body, mode="matter")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/continuity", response_model=RecallCard)
def recall_continuity(body: RecallRequest) -> RecallCard:
    """Continuity-mode recall — boot journal + open todos, no burst dispositions."""
    try:
        return _handle_recall(body, mode="continuity")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
