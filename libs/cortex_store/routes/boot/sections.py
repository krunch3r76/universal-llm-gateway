"""GET /boot-sections — salience-driven entity sections for boot briefings."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from universal_logging import get_logger

from ...db import cortex_conn
from ...salience import SalienceResult, compute_all_salience
from ._render import (
    _advance_slow_state,
    _build_full_sections,
    _build_oneline_sections,
)

logger = get_logger("cortex-api.boot")
router = APIRouter(tags=["boot"])


@router.get("/boot-sections")
def get_boot_sections(
    persona: str = Query("web", description="Salience weight profile"),
    agent: str = Query("web", description="Agent for contextual scoring"),
    session_id: str | None = Query(
        None, description="Session ID for contextual scoping"
    ),
    max_full: int = Query(5, ge=1, le=20, description="Max full_section entities"),
    max_oneline: int = Query(15, ge=1, le=50, description="Max one_line entities"),
    type_exclude: str | None = Query(
        None, description="Comma-separated entity types to exclude"
    ),
) -> dict[str, Any]:
    """Salience-driven entity sections for boot briefings.

    Computes salience, applies cold-start caps, renders section Markdown,
    and advances slow state for all entities (boot = observation point).
    """
    t_now = datetime.now(UTC)
    excluded_types: set[str] = set()
    if type_exclude:
        excluded_types = {t.strip() for t in type_exclude.split(",") if t.strip()}

    conn = cortex_conn()
    try:
        results, hits, misses = compute_all_salience(
            conn,
            persona=persona,
            t_now=t_now,
            agent=agent,
            session_id=session_id,
        )

        if excluded_types:
            results = [r for r in results if r.entity_type not in excluded_types]

        full_results: list[SalienceResult] = []
        oneline_results: list[SalienceResult] = []
        for r in results:
            if r.boot_treatment == "full_section" and len(full_results) < max_full:
                full_results.append(r)
            elif len(oneline_results) < max_oneline:
                oneline_results.append(r)

        full_sections = _build_full_sections(conn, full_results, t_now)
        oneline_sections = _build_oneline_sections(conn, oneline_results, t_now)

        advanced = _advance_slow_state(conn)
        if advanced:
            logger.info("Boot slow state advanced for %d entities", advanced)
    finally:
        conn.close()

    return {
        "persona": persona,
        "computed_at": t_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entity_count": hits + misses,
        "sections": {
            "full": full_sections,
            "oneline": oneline_sections,
        },
        "cache_stats": {
            "full_count": len(full_sections),
            "oneline_count": len(oneline_sections),
            "total_scored": hits + misses,
            "slow_state_advanced": advanced,
        },
    }
