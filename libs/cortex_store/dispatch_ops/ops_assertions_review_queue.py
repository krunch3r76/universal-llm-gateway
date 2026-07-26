"""Review-queue aggregation for assertion and entity quality surfacing."""

from __future__ import annotations

import json
from typing import Any

from universal_logging import get_logger

from ..db import cortex_conn, query
from ..routes.assertions import _list_assertions_impl
from ..status_trait_read import effective_confidence_band
from .ops_entities import _op_entities

logger = get_logger("cortex-api.dispatch_ops.assertions")


def _op_review_queue(
    limit: int | None = None,
    include_compaction_pointers: bool = False,
    **_: object,
) -> dict[str, Any]:
    lim = limit or 30
    # todo:cortex-aggregate-compaction-filter — these are aggregate (no
    # entity_id) reads; pointer rows are filtered by `list_assertions` itself
    # unless the override is requested.
    flagged_resp = _list_assertions_impl(
        review_status="flagged",
        superseded=False,
        limit=lim,
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    staged_resp = _list_assertions_impl(
        review_status="staged",
        superseded=False,
        limit=lim,
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    low_conf_resp = _list_assertions_impl(
        superseded=False,
        limit=lim,
        intent="full",
        include_compaction_pointers=include_compaction_pointers,
    )
    entities = _op_entities(limit=lim)
    flagged = (
        [
            {**a, "priority": 2, "reason": "flagged"}
            for a in flagged_resp.get("items", [])
        ]
        if not flagged_resp.get("error")
        else []
    )
    staged = (
        [
            {**a, "priority": 1, "reason": "staged (quality warning)"}
            for a in staged_resp.get("items", [])
        ]
        if not staged_resp.get("error")
        else []
    )
    endeavor_pending = []
    try:
        with cortex_conn() as conn:
            rows = query(
                conn,
                "SELECT id, entity_id, claim, predicate_form, attributes, "
                "resolution_status, review_status "
                "FROM assertions "
                "WHERE superseded_by IS NULL AND resolution_status = 'pending' "
                "AND predicate_form LIKE 'endeavor_strategy_row(%' "
                "LIMIT ?",
                (lim,),
            )
        for a in rows:
            attrs_raw = a.get("attributes")
            attrs = {}
            if isinstance(attrs_raw, str):
                try:
                    attrs = json.loads(attrs_raw) if attrs_raw else {}
                except json.JSONDecodeError:
                    attrs = {}
            elif isinstance(attrs_raw, dict):
                attrs = attrs_raw
            endeavor_pending.append(
                {
                    **a,
                    "priority": 0,
                    "reason": "endeavor_strategy_row_pending",
                    "host": a.get("entity_id"),
                    "row_id": attrs.get("row_id"),
                    "affects": attrs.get("affects"),
                }
            )
    except Exception:  # noqa: BLE001 — review_queue must stay best-effort
        logger.warning("endeavor pending row surfacing failed", exc_info=True)

    low_conf = []
    if not low_conf_resp.get("error"):
        for a in low_conf_resp.get("items", []):
            if a.get("confidence") in ("suspected", "hypothesized"):
                low_conf.append({**a, "priority": 3, "reason": "low_confidence"})

    provisional = []
    thin_descriptions = []
    if not entities.get("error"):
        for e in entities.get("items", []):
            band = effective_confidence_band(e)
            if band == "provisional":
                provisional.append({**e, "priority": 4, "reason": "provisional"})
            desc = e.get("description") or ""
            if len(desc) < 50:
                thin_descriptions.append(
                    {**e, "priority": 5, "reason": "thin_description"}
                )

    total = (
        len(endeavor_pending)
        + len(flagged)
        + len(staged)
        + len(provisional)
        + len(low_conf)
        + len(thin_descriptions)
    )
    return {
        "provisional_entities": provisional,
        "flagged_assertions": flagged,
        "staged_assertions": staged,
        "endeavor_pending_rows": endeavor_pending,
        "low_confidence_assertions": low_conf,
        "thin_descriptions": thin_descriptions,
        "total": total,
    }


__all__ = ["_op_review_queue"]
