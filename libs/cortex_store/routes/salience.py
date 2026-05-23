"""GET /salience endpoint — compute and return entity salience scores."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from universal_logging import get_logger

from ..db import cortex_conn
from ..salience import SalienceResult, compute_all_salience

logger = get_logger("cortex-api.salience")
router = APIRouter(tags=["salience"])


def _result_to_dict(r: SalienceResult) -> dict:
    return {
        "entity_id": r.entity_id,
        "entity_name": r.entity_name,
        "entity_type": r.entity_type,
        "salience_score": round(r.salience_score, 4),
        "temporal_score": round(r.temporal_score, 4),
        "structural_score": round(r.structural_score, 4),
        "contextual_score": round(r.contextual_score, 4),
        "frequency_score": round(r.frequency_score, 4),
        "surprise": round(r.surprise, 4),
        "boot_treatment": r.boot_treatment,
        "domain": r.domain,
    }


@router.get("/salience")
def get_salience(
    persona: str = Query("web", description="Weight profile: web, cursor, api"),
    entity_id: str | None = Query(None, description="Compute for a single entity"),
    force: bool = Query(False, description="Ignore fingerprint cache"),
    top_k: int | None = Query(None, ge=1, description="Return only top K entities"),
    agent: str = Query("web", description="Agent persona for contextual scoring"),
    session_id: str | None = Query(
        None, description="Session ID for contextual scoping"
    ),
) -> dict:
    """Compute composite salience scores for all (or one) Cortex entities."""
    t_now = datetime.now(UTC)
    with cortex_conn() as conn:
        results, hits, misses = compute_all_salience(
            conn,
            persona=persona,
            t_now=t_now,
            force=force,
            entity_id_filter=entity_id,
            agent=agent,
            session_id=session_id,
        )

    if top_k is not None:
        results = results[:top_k]

    return {
        "persona": persona,
        "computed_at": t_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entity_count": hits + misses,
        "cache_hits": hits,
        "cache_misses": misses,
        "results": [_result_to_dict(r) for r in results],
    }
