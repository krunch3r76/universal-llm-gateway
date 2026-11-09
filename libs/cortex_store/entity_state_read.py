"""Read rule ``field_state`` for live entity structured fields (W4 decision 13)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from cortex_store.card_temporal import (
    card_as_of_utc,
    install_card_temporal_function,
    instant_key,
)
from cortex_store.db import decode_row
from cortex_store.db import query as db_query
from cortex_store.entity_crud import ENTITY_JSON_FIELDS
from cortex_store.entity_state_flag_rule import flag_withholds
from cortex_store.entity_state_fold_gate import ResolveUtterance, gate_refusal
from cortex_store.entity_state_provenance import (
    FOLD_FIELDS,
    claim_binds_field,
    parse_attributes,
    parse_status_current,
    provenance_head,
    records_for_field,
    values_match_strict,
)


@dataclass(frozen=True, slots=True)
class FieldState:
    """Read-rule projection for one structured field (decision 13)."""

    value: object | None
    present: bool
    verification: Literal["verified", "unverified"]
    head_ref: str | None
    newer_claim_id: int | None
    marker: str | None = None
    newer_review_status: str | None = None
    newer_claim_created_at: str | None = None


def _field_verified(
    attrs: dict[str, Any], field: str, workflow_state: str | None
) -> tuple[Literal["verified", "unverified"], dict[str, Any] | None]:
    records = records_for_field(attrs, field)
    ids = [r.get("provenance_id") for r in records if r.get("provenance_id")]
    if len(ids) != len(set(ids)):
        return "unverified", None
    head = provenance_head(records)
    if head is None:
        return "unverified", None
    if not head.get("ref") or not head.get("ref_kind"):
        return "unverified", head
    current = workflow_state if field == "workflow_state" else attrs.get(field)
    if not values_match_strict(current, head.get("value")):
        return "unverified", head
    return "verified", head


def _fetch_claim_rows(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    rows = db_query(
        conn,
        "SELECT * FROM assertions WHERE entity_id = ? AND superseded_by IS NULL",
        (entity_id,),
    )
    out = [dict(r) for r in rows]

    def _sort_key(r: dict[str, Any]) -> tuple[object, int]:
        try:
            ts = instant_key(r.get("created_at"))
        except ValueError:
            ts = instant_key("1970-01-01T00:00:00+00:00")
        return ts, int(r["id"])

    out.sort(key=_sort_key, reverse=True)
    return out


def _claim_eligible_for_field_state(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    field: str,
    row: dict[str, Any],
    state_provenance: dict[str, Any],
    resolver: ResolveUtterance | None,
) -> bool:
    if flag_withholds(row.get("review_status"), row.get("review_notes"), row):
        return False
    pf = str(row.get("predicate_form") or "")
    parsed = parse_status_current(pf)
    if parsed is None:
        return False
    bound_entity, token = parsed
    if bound_entity != entity_id or str(row.get("entity_id")) != entity_id:
        return False
    if not claim_binds_field(field, entity_id, pf, token):
        return False
    if gate_refusal(
        conn,
        entity_id=entity_id,
        entity_type=entity_type,
        field=field,
        row=row,
        state_provenance=state_provenance,
        resolver=resolver,
        as_of=card_as_of_utc(),
        fail_closed_on_missing_head=False,
    ):
        return False
    records = state_provenance.get(field)
    if isinstance(records, list):
        revs = {r.get("reverses") for r in records if r.get("kind") == "reversal"}
        for rec in records:
            if rec.get("kind") == "fold" and rec.get("assertion_id") == row.get("id"):
                if rec.get("provenance_id") in revs:
                    return False
    return True


def _field_state_core(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    entity_type: str,
    attrs: dict[str, Any],
    workflow_state: str | None,
    field: str,
    claim_rows: list[dict[str, Any]],
    state_provenance: dict[str, Any],
    resolver: ResolveUtterance | None,
) -> FieldState:
    verification, head = _field_verified(attrs, field, workflow_state)
    head_ref = str(head.get("ref")) if head and head.get("ref") else None
    marker = str(head.get("marker")) if head and head.get("marker") else None
    if field == "workflow_state":
        value, present = workflow_state, workflow_state is not None
    else:
        val = attrs.get(field)
        present = field in attrs and val is not None
        value = val
    newest_id: int | None = None
    newest_rs: str | None = None
    newest_created: str | None = None
    for row in claim_rows:
        if _claim_eligible_for_field_state(
            conn,
            entity_id=entity_id,
            entity_type=entity_type,
            field=field,
            row=row,
            state_provenance=state_provenance,
            resolver=resolver,
        ):
            newest_id = int(row["id"])
            newest_rs = (
                str(row.get("review_status")) if row.get("review_status") is not None else None
            )
            newest_created = str(row.get("created_at") or "") or None
            break
    newer_out: int | None = None
    newer_rs_out: str | None = None
    newer_created_out: str | None = None
    if verification == "unverified":
        newer_out = newest_id
        newer_rs_out = newest_rs
        newer_created_out = newest_created
    elif newest_id is not None and head and head.get("assertion_id") != newest_id:
        newer_out = newest_id
        newer_rs_out = newest_rs
        newer_created_out = newest_created
    return FieldState(
        value=value,
        present=present,
        verification=verification,
        head_ref=head_ref,
        newer_claim_id=newer_out,
        marker=marker,
        newer_review_status=newer_rs_out,
        newer_claim_created_at=newer_created_out,
    )


def field_states(
    conn: sqlite3.Connection,
    entity_id: str,
    fields: frozenset[str] = FOLD_FIELDS,
    *,
    resolver: ResolveUtterance | None = None,
) -> dict[str, FieldState]:
    """Batch field_state for fold fields (one entity SELECT, one claim fetch)."""
    install_card_temporal_function(conn)
    ent = conn.execute(
        "SELECT type, attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if ent is None:
        return {}
    attrs = parse_attributes(ent["attributes"])
    sp = attrs.get("state_provenance")
    state_provenance = dict(sp) if isinstance(sp, dict) else {}
    entity_type = str(ent["type"])
    claim_rows = _fetch_claim_rows(conn, entity_id)
    out: dict[str, FieldState] = {}
    for field in fields:
        out[field] = _field_state_core(
            conn,
            entity_id=entity_id,
            entity_type=entity_type,
            attrs=attrs,
            workflow_state=ent["workflow_state"],
            field=field,
            claim_rows=claim_rows,
            state_provenance=state_provenance,
            resolver=resolver,
        )
    return out


def field_state(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    *,
    resolver: ResolveUtterance | None = None,
) -> FieldState:
    """Read-only field head + newest eligible claim (decision 13)."""
    states = field_states(conn, entity_id, frozenset({field}), resolver=resolver)
    if field not in states:
        return FieldState(None, False, "unverified", None, None)
    return states[field]


def read_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, object] | None:
    rows = db_query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
    if not rows:
        return None
    return decode_row(rows[0], ENTITY_JSON_FIELDS)


def read_assertion(conn: sqlite3.Connection, assertion_id: int) -> dict[str, object] | None:
    rows = db_query(conn, "SELECT * FROM assertions WHERE id = ?", (assertion_id,))
    if not rows:
        return None
    return dict(rows[0])
