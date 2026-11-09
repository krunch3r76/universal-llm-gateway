"""W5 live status sentence render-on-read (summary_row / preview / predicate_summary)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from cortex_store.entity_state_fold_gate import entity_is_concern
from cortex_store.entity_state_provenance import (
    FOLD_FIELDS,
    parse_attributes,
    records_for_field,
)
from cortex_store.entity_state_read import FieldState, field_states

_APP_SUBMIT_SUPPRESS = frozenset({"submitted", "not_submitted", "unsubmitted"})


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _render_field_part(field: str, fs: FieldState) -> str | None:
    if field == "workflow_state" and fs.value is None and not fs.newer_claim_id:
        return None
    if not fs.present and fs.newer_claim_id is None:
        return None
    head_bit = f", head {fs.head_ref}" if fs.head_ref else ""
    body = f"{field}={_format_value(fs.value)} [{fs.verification}{head_bit}]"
    if fs.newer_claim_id is not None:
        created = fs.newer_claim_created_at or ""
        rs = fs.newer_review_status if fs.newer_review_status is not None else ""
        body += f" · newer claim a:{fs.newer_claim_id} ({created}, review_status={rs})"
    return body


def _should_render_workflow_state(fs: FieldState, attrs: dict[str, Any]) -> bool:
    if fs.newer_claim_id is not None:
        return True
    if records_for_field(attrs, "workflow_state"):
        return fs.present or fs.newer_claim_id is not None
    return False


def _build_suppress_tokens(
    rendered: frozenset[str],
) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    if "application_submitted" in rendered:
        out["application_submitted"] = _APP_SUBMIT_SUPPRESS
    if "workflow_state" in rendered:
        out["workflow_state"] = frozenset()
    return out


@dataclass(frozen=True, slots=True)
class StatusView:
    """Derived status sentence and fold field projections for one entity."""

    sentence: str
    field_states: dict[str, FieldState]
    rendered_fields: frozenset[str]
    suppress_status_tokens: dict[str, frozenset[str]]
    conflict_review_status: str | None = None


def derive_status_view(
    conn: sqlite3.Connection,
    entity_id: str,
    entity_row: dict[str, Any],
) -> StatusView | None:
    """Compute the live status sentence; None when no fold field renders."""
    entity_type = str(entity_row.get("type") or "")
    if entity_is_concern(conn, entity_id, entity_type):
        return None
    attrs = parse_attributes(entity_row.get("attributes"))
    states = field_states(conn, entity_id, FOLD_FIELDS)
    parts: list[str] = []
    rendered: set[str] = set()
    conflict_rs: str | None = None
    for field in sorted(FOLD_FIELDS):
        fs = states.get(field)
        if fs is None:
            continue
        if field == "workflow_state":
            if not _should_render_workflow_state(fs, attrs):
                continue
        else:
            if not fs.present and fs.newer_claim_id is None:
                continue
        segment = _render_field_part(field, fs)
        if segment is None:
            continue
        parts.append(segment)
        rendered.add(field)
        if fs.newer_claim_id is not None and conflict_rs is None:
            conflict_rs = fs.newer_review_status
    if not parts:
        return None
    sentence = "; ".join(parts)
    return StatusView(
        sentence=sentence,
        field_states=states,
        rendered_fields=frozenset(rendered),
        suppress_status_tokens=_build_suppress_tokens(frozenset(rendered)),
        conflict_review_status=conflict_rs,
    )
