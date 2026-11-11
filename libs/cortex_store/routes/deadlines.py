from __future__ import annotations

import re
import sqlite3
from typing import Any

from fastapi import APIRouter, Query
from openapi_mcp.binding import x_mcp
from universal_logging import get_logger

from ..action_hints import detect_deadline_resolution
from ..db import cortex_conn, query
from ..entity_state_provenance import (
    parse_attributes,
    provenance_head,
    records_for_field,
    values_match_strict,
)
from ..models import DeadlineItem, DeadlineList
from ..models.substrate_s6_batch3 import DeadlineResolveRequest

logger = get_logger("cortex-api.deadlines")
router = APIRouter(prefix="/deadlines", tags=["deadlines"])

_RESOLVED_OUTCOMES = frozenset({"defaulted", "met", "withdrawn", "superseded"})
_OPEN_WORKFLOW_STATES = frozenset({"open", "in_progress"})
_RESOLVED_CLAIM_RE = re.compile(r"\bRESOLVED\b")
_RESOLVED_VIA_ORDER = ("urgency", "outcome", "resolved_claim", "fold")
_ROW_POP_KEYS = (
    "deadline_attributes",
    "deadline_workflow_state",
    "subject_attributes",
    "edge_active",
)
_IN_CLAUSE_CHUNK = 500

# Two attribute-key shapes exist on deadline entities:
#   - `$.date` (older deadlines created pre-2026-04)
#   - `$.deadline_date` (newer deadlines, including BOE-19-P 2026-05-05)
# COALESCE both so neither shape silently drops out of the boot card.
#
# Includes deadline_id for the assertion-based resolution filter.
# ∀ entity d with type-relationship 'deadline_for' m: d.id is needed so
# _resolved_assertion_ids() can check confirmed RESOLVED assertions on d directly.
#
# UNION arm 1 surfaces any entity that a deadline points at via `deadline_for`.
# The join key is the relationship type, NOT the target entity type: a deadline's
# subject can be a legal_matter, case, person, finance, or any future type. A
# `m.type = 'legal_matter'` filter here silently dropped case/person/finance
# deadlines from every read-back surface (op, route, boot card) even though they
# were durably written (friction 24249). The `matter_id`/`matter_name` aliases are
# retained for render-shape stability but now generalize to the deadline's subject.
#
# UNION arm 2 surfaces todo entities that carry their own `deadline_date`
# attribute (and aren't already routed through a linked deadline entity).
# Per agent-bus thread 882 (option A): one unified ranked list of date-bearing
# items — agents reading the boot don't care about the source-table distinction.
# The matter_id/matter_name cells are repurposed to point at the todo itself
# so the briefing card render shape is unchanged.
_DEADLINES_SQL = """
    SELECT
        m.id AS matter_id,
        m.name AS matter_name,
        d.id AS deadline_id,
        d.name AS deadline_name,
        COALESCE(
            json_extract(d.attributes, '$.deadline_date'),
            json_extract(d.attributes, '$.date')
        ) AS deadline_date,
        json_extract(d.attributes, '$.description') AS deadline_description,
        json_extract(d.attributes, '$.urgency') AS urgency,
        json_extract(d.attributes, '$.outcome') AS outcome,
        d.attributes AS deadline_attributes,
        d.workflow_state AS deadline_workflow_state,
        m.attributes AS subject_attributes,
        COALESCE(r.active, 1) AS edge_active
    FROM entities m
    JOIN relationships r ON r.to_entity = m.id AND r.type = 'deadline_for'
    JOIN entities d ON r.from_entity = d.id

    UNION ALL

    SELECT
        t.id AS matter_id,
        t.name AS matter_name,
        t.id AS deadline_id,
        t.name AS deadline_name,
        json_extract(t.attributes, '$.deadline_date') AS deadline_date,
        NULL AS deadline_description,
        json_extract(t.attributes, '$.urgency') AS urgency,
        json_extract(t.attributes, '$.outcome') AS outcome,
        t.attributes AS deadline_attributes,
        t.workflow_state AS deadline_workflow_state,
        t.attributes AS subject_attributes,
        1 AS edge_active
    FROM entities t
    WHERE t.type = 'todo'
      AND json_extract(t.attributes, '$.deadline_date') IS NOT NULL
      AND (t.workflow_state IS NULL OR t.workflow_state IN ('open', 'in_progress'))

    ORDER BY deadline_date
"""


def _head_closes(attrs: dict[str, Any], field: str, current: object | None) -> bool:
    """True when a verified fold provenance head closes the field (W6 checks 1–5)."""
    records = records_for_field(attrs, field)
    provenance_ids = [r.get("provenance_id") for r in records if r.get("provenance_id")]
    if len(provenance_ids) != len(set(provenance_ids)):
        return False
    head = provenance_head(records)
    if head is None:
        return False
    if not head.get("ref") or not head.get("ref_kind"):
        return False
    if not values_match_strict(current, head.get("value")):
        return False
    return head.get("kind") == "fold"


def _fold_assertion_id(attrs: dict[str, Any], field: str) -> int | None:
    head = provenance_head(records_for_field(attrs, field))
    if head is None:
        return None
    aid = head.get("assertion_id")
    return aid if isinstance(aid, int) else None


def _workflow_state_fold_closes(deadline_attrs_raw: object | None, deadline_wf: str | None) -> bool:
    attrs = parse_attributes(deadline_attrs_raw)
    if not _head_closes(attrs, "workflow_state", deadline_wf):
        return False
    head = provenance_head(records_for_field(attrs, "workflow_state"))
    if head is None:
        return False
    val = head.get("value")
    return isinstance(val, str) and bool(val) and val not in _OPEN_WORKFLOW_STATES


def _application_submitted_fold_closes(
    subject_attrs_raw: object | None, *, edge_active: int
) -> bool:
    if edge_active != 1:
        return False
    attrs = parse_attributes(subject_attrs_raw)
    current = attrs.get("application_submitted")
    if not _head_closes(attrs, "application_submitted", current):
        return False
    head = provenance_head(records_for_field(attrs, "application_submitted"))
    return head is not None and head.get("value") is True


def _resolved_assertion_ids(
    conn: sqlite3.Connection, deadline_ids: list[str]
) -> dict[str, list[int]]:
    """Map deadline entity id → confirmed RESOLVED assertion ids (word-boundary match)."""
    if not deadline_ids:
        return {}
    out: dict[str, list[int]] = {}
    unique_ids = list(dict.fromkeys(deadline_ids))
    for offset in range(0, len(unique_ids), _IN_CLAUSE_CHUNK):
        chunk = unique_ids[offset : offset + _IN_CLAUSE_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = query(
            conn,
            f"SELECT id, entity_id, claim FROM assertions "
            f"WHERE entity_id IN ({placeholders}) "
            f"AND confidence = 'confirmed' "
            f"AND UPPER(claim) LIKE '%RESOLVED%' "
            f"AND superseded_by IS NULL "
            f"ORDER BY id",
            tuple(chunk),
        )
        for row in rows:
            claim = row.get("claim") or ""
            if not _RESOLVED_CLAIM_RE.search(str(claim).upper()):
                continue
            eid = str(row["entity_id"])
            out.setdefault(eid, []).append(int(row["id"]))
    for eid, ids in out.items():
        out[eid] = sorted(set(ids))
    return out


def _merge_resolved_by(fold_ids: list[int], claim_ids: list[int]) -> list[int]:
    seen: set[int] = set()
    merged: list[int] = []
    for aid in fold_ids + sorted(claim_ids):
        if aid in seen:
            continue
        seen.add(aid)
        merged.append(aid)
    return merged


def _deadline_row_resolution(
    row: dict[str, Any], claim_ids: list[int]
) -> tuple[bool, list[int], list[str]]:
    """Return (is_closed, resolved_by assertion ids, resolved_via tags) for one SQL row."""
    resolved_via: list[str] = []
    fold_ids: list[int] = []

    urgency = row.get("urgency")
    outcome = row.get("outcome")
    if urgency == "resolved":
        resolved_via.append("urgency")
    if outcome and outcome in _RESOLVED_OUTCOMES:
        resolved_via.append("outcome")

    if claim_ids:
        resolved_via.append("resolved_claim")

    if _workflow_state_fold_closes(row.get("deadline_attributes"), row.get("deadline_workflow_state")):
        resolved_via.append("fold")
        d_attrs = parse_attributes(row.get("deadline_attributes"))
        aid = _fold_assertion_id(d_attrs, "workflow_state")
        if aid is not None:
            fold_ids.append(aid)

    edge_active = int(row.get("edge_active") or 0)
    if _application_submitted_fold_closes(row.get("subject_attributes"), edge_active=edge_active):
        if "fold" not in resolved_via:
            resolved_via.append("fold")
        s_attrs = parse_attributes(row.get("subject_attributes"))
        aid = _fold_assertion_id(s_attrs, "application_submitted")
        if aid is not None:
            fold_ids.append(aid)

    resolved_via_sorted = sorted(
        set(resolved_via),
        key=lambda tag: _RESOLVED_VIA_ORDER.index(tag),
    )
    is_closed = bool(resolved_via)
    resolved_by = _merge_resolved_by(fold_ids, claim_ids)
    return is_closed, resolved_by, resolved_via_sorted


def _deadline_item_from_row(
    row: dict[str, Any], *, resolved_by: list[int], resolved_via: list[str]
) -> DeadlineItem:
    payload = {k: v for k, v in row.items() if k not in _ROW_POP_KEYS}
    payload["resolved_by"] = resolved_by
    payload["resolved_via"] = resolved_via
    return DeadlineItem(**payload)


def _is_resolved(item: DeadlineItem) -> bool:
    if item.urgency == "resolved":
        return True
    if item.outcome and item.outcome in _RESOLVED_OUTCOMES:
        return True
    return False


@router.get("", response_model=DeadlineList, openapi_extra=x_mcp("deadlines"))
def list_deadlines(
    include_resolved: bool = Query(False, description="Include resolved deadlines"),
) -> DeadlineList:
    """Return active deadlines for urgency-aware planning.

    Excludes deadlines with urgency='resolved', terminal outcomes
    (defaulted, met, withdrawn, superseded), a confirmed RESOLVED assertion
    on the deadline entity, or a verified entity-state fold head — unless
    include_resolved=true.
    Enriches the response with action_hints when overdue deadlines have
    resolution language in the matter's active assertions.
    """
    conn = cortex_conn()
    try:
        rows = query(conn, _DEADLINES_SQL)
        row_dicts = [dict(r) for r in rows]
        deadline_ids = [
            str(r["deadline_id"]) for r in row_dicts if r.get("deadline_id")
        ]
        claim_map = _resolved_assertion_ids(conn, deadline_ids)

        all_items: list[DeadlineItem] = []
        for row in row_dicts:
            dl_id = str(row.get("deadline_id") or "")
            claim_ids = claim_map.get(dl_id, [])
            is_closed, resolved_by, resolved_via = _deadline_row_resolution(row, claim_ids)
            item = _deadline_item_from_row(
                row, resolved_by=resolved_by, resolved_via=resolved_via
            )
            if include_resolved or not is_closed:
                all_items.append(item)

        hints = detect_deadline_resolution(
            [item.model_dump() for item in all_items], conn
        )
    finally:
        conn.close()

    return DeadlineList(items=all_items, action_hints=hints or None)


def _list_deadlines_impl(*, include_resolved: bool = False) -> dict[str, object]:
    return list_deadlines(include_resolved=include_resolved).model_dump(mode="json")


@router.post("/{deadline_id}/resolve", openapi_extra=x_mcp("deadline_resolve"))
def deadline_resolve_route(
    deadline_id: str,
    body: DeadlineResolveRequest,
) -> dict[str, Any]:
    """Atomically close a deadline entity (dispatch ``deadline_resolve`` op)."""
    from ..dispatch_ops.ops_journals import _op_deadline_resolve

    return _op_deadline_resolve(
        deadline_id=deadline_id,
        **body.model_dump(exclude_unset=True),
    )
