"""Read rule ``field_state`` for live entity structured fields (W4 decision 13)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from cortex_store.card_temporal import card_as_of_utc, install_card_temporal_function
from cortex_store.db import query as db_query
from cortex_store.entity_state_flag_rule import flag_withholds
from cortex_store.entity_state_fold_gate import ResolveUtterance, gate_refusal
from cortex_store.entity_state_provenance import (
    claim_binds_field,
    parse_attributes,
    parse_status_current,
    provenance_head,
    records_for_field,
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
    head_val = head.get("value")
    if not (current is None and head_val is None) and current != head_val:
        return "unverified", head
    return "verified", head


def _fetch_claim_rows(conn: sqlite3.Connection, entity_id: str) -> list[dict[str, Any]]:
    rows = db_query(
        conn,
        "SELECT * FROM assertions WHERE entity_id = ? ORDER BY created_at DESC, id DESC",
        (entity_id,),
    )
    return [dict(r) for r in rows]


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


def field_state(
    conn: sqlite3.Connection,
    entity_id: str,
    field: str,
    *,
    resolver: ResolveUtterance | None = None,
) -> FieldState:
    """Read-only field head + newest eligible claim (decision 13)."""
    install_card_temporal_function(conn)
    ent = conn.execute(
        "SELECT type, attributes, workflow_state FROM entities WHERE id = ?",
        (entity_id,),
    ).fetchone()
    if ent is None:
        return FieldState(None, False, "unverified", None, None)
    attrs = parse_attributes(ent["attributes"])
    sp = attrs.get("state_provenance")
    state_provenance = dict(sp) if isinstance(sp, dict) else {}
    verification, head = _field_verified(attrs, field, ent["workflow_state"])
    if (
        verification == "verified"
        and head
        and head.get("ref_kind") == "assertion"
        and head.get("ref")
    ):
        ref = str(head["ref"])
        if ref.startswith("assertion:"):
            try:
                head_aid = int(ref.split(":", 1)[1])
            except ValueError:
                verification = "unverified"
            else:
                if (
                    conn.execute(
                        "SELECT 1 FROM assertions WHERE id = ?", (head_aid,)
                    ).fetchone()
                    is None
                ):
                    verification = "unverified"
    head_ref = str(head.get("ref")) if head and head.get("ref") else None
    marker = str(head.get("marker")) if head and head.get("marker") else None
    if field == "workflow_state":
        value, present = ent["workflow_state"], ent["workflow_state"] is not None
    else:
        val = attrs.get(field)
        present = field in attrs and val is not None
        value = val
    newest_id: int | None = None
    for row in _fetch_claim_rows(conn, entity_id):
        if _claim_eligible_for_field_state(
            conn,
            entity_id=entity_id,
            entity_type=str(ent["type"]),
            field=field,
            row=row,
            state_provenance=state_provenance,
            resolver=resolver,
        ):
            newest_id = int(row["id"])
            break
    newer_out: int | None = None
    if verification == "unverified":
        newer_out = newest_id
    elif newest_id is not None and head and head.get("assertion_id") != newest_id:
        newer_out = newest_id
    return FieldState(
        value=value,
        present=present,
        verification=verification,
        head_ref=head_ref,
        newer_claim_id=newer_out,
        marker=marker,
    )
