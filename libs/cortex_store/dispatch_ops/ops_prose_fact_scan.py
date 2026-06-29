"""Dispatch op: prose_fact_scan — stale living-context prose detector."""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ..entity_crud import create_entity_impl
from ..prose_fact_scan.constants import SERVICE_ENTITY_ID
from ..prose_fact_scan.scanner import run_prose_fact_scan
from ._shared import record
from .ops_assertions import _op_assertions
from .ops_assertions_write import _op_friction


def _ensure_scanner_service() -> None:
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT id FROM entities WHERE id = ?", (SERVICE_ENTITY_ID,)
        ).fetchone()
        if row:
            return
        create_entity_impl(
            conn,
            {
                "id": SERVICE_ENTITY_ID,
                "type": "service",
                "name": "Prose Fact Scanner",
                "description": (
                    "Detects stale operational prose against active assertions"
                ),
            },
        )


def _default_fetch(entity_id: str) -> list[dict[str, Any]]:
    result = _op_assertions(
        entity_id=entity_id,
        superseded=False,
        limit=50,
        intent="full",
    )
    if result.get("error"):
        return []
    items = result.get("items") or result.get("assertions") or []
    return items if isinstance(items, list) else []


def _default_search(query: str) -> list[dict[str, Any]]:
    from .ops_assertions import _op_search

    result = _op_search(query=query, limit=6)
    if result.get("error"):
        return []
    items = result.get("items") or []
    return items if isinstance(items, list) else []


def _default_analyze_impact(entity_id: str, claim: str) -> float:
    from .ops_assertions import _op_analyze_impact

    result = _op_analyze_impact(
        entity_id=entity_id,
        claim=claim,
        confidence="confirmed",
    )
    if result.get("error"):
        return 0.0
    return float(result.get("alignment_score") or result.get("score") or 0.0)


def _op_prose_fact_scan(
    principal: str | None = None,
    paths: list[str] | None = None,
    tier: str | None = None,
    dry_run: bool = False,
    unsafe_full_scan: bool = False,
    **_: object,
) -> dict[str, Any]:
    _ensure_scanner_service()
    record("cortex.prose_fact_scan.started", dry_run=dry_run)
    result = run_prose_fact_scan(
        principal=principal,
        paths=paths,
        tier=tier,
        dry_run=dry_run,
        unsafe_full_scan=unsafe_full_scan,
        fetch_fn=_default_fetch,
        search_fn=_default_search,
        analyze_impact_fn=_default_analyze_impact,
        friction_fn=_op_friction,
    )
    if "error" not in result:
        record(
            "cortex.prose_fact_scan.completed",
            dry_run=dry_run,
            target_count=result.get("target_count"),
            finding_count=result.get("finding_count"),
        )
    return result
