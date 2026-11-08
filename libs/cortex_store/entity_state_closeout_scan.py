"""F3 closeout scan for host runs (not imported by fold hot path)."""

from __future__ import annotations

import sqlite3

from cortex_store.card_temporal import card_as_of_utc, install_card_temporal_function
from cortex_store.drift_detector import scan_entity_state_drift
from cortex_store.entity_state_flag_rule import CONTRADICTION_PREFIXES, flag_withholds
from cortex_store.entity_state_fold_gate import gate_refusal, parse_status_current
from cortex_store.entity_state_provenance import claim_binds_field

_FOLD_FIELDS = ("application_submitted", "workflow_state")


def _f3_newer_from_drift(conn: sqlite3.Connection) -> tuple[int, set[int]]:
    drift = scan_entity_state_drift(conn=conn)
    f3_rows = sum(1 for r in drift.rows if r.drift_class == "F3")
    newer: set[int] = set()
    for row in drift.rows:
        if row.drift_class != "F3":
            continue
        cite = row.right_cite or ""
        if not cite.startswith("assertion:"):
            continue
        try:
            newer.add(int(cite.split(":", 1)[1]))
        except ValueError:
            continue
    return f3_rows, newer


def _fold_field_for_row(entity_id: str, predicate_form: str) -> str | None:
    parsed = parse_status_current(predicate_form)
    if parsed is None:
        return None
    bound_entity, token = parsed
    if bound_entity != entity_id:
        return None
    for field in _FOLD_FIELDS:
        if claim_binds_field(field, entity_id, predicate_form, token):
            return field
    return None


def _count_contradiction_chunks(notes: str) -> tuple[int, int, int, int]:
    j_contradiction = j_cross = j_semantic = flagged_total = 0
    for chunk in [c.strip() for c in notes.split(";") if c.strip()]:
        lower = chunk.lower()
        for i, prefix in enumerate(CONTRADICTION_PREFIXES):
            if lower.startswith(prefix.lower()):
                flagged_total += 1
                if i == 0:
                    j_contradiction += 1
                elif i == 1:
                    j_cross += 1
                else:
                    j_semantic += 1
                break
    return j_contradiction, j_cross, j_semantic, flagged_total


def scan_live_w4_flagged_closeout_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Read-only F3 closeout counters (``flagged_foldable`` ⊆ F3 newer claims)."""
    install_card_temporal_function(conn)
    as_of = card_as_of_utc()
    f3_rows, f3_newer = _f3_newer_from_drift(conn)
    reasonless = predicate_normalize = other = 0
    flagged_foldable = 0
    contradict_like = 0
    j_contradiction = j_cross = j_semantic = 0
    contradiction_chunk_flagged = 0

    for row in conn.execute(
        "SELECT review_notes FROM assertions WHERE review_status = 'flagged'"
    ):
        notes = str(row[0] or "")
        c, x, s, n = _count_contradiction_chunks(notes)
        j_contradiction += c
        j_cross += x
        j_semantic += s
        contradiction_chunk_flagged += n

    for row in conn.execute(
        "SELECT a.*, e.type AS entity_type FROM assertions a "
        "JOIN entities e ON e.id = a.entity_id "
        "WHERE a.review_status = 'flagged' AND a.superseded_by IS NULL"
    ):
        rd = dict(row)
        aid = int(rd["id"])
        notes = str(rd.get("review_notes") or "")
        if flag_withholds(rd.get("review_status"), notes, rd):
            continue
        if aid not in f3_newer:
            continue
        entity_id = str(rd["entity_id"])
        pf = str(rd.get("predicate_form") or "")
        field = _fold_field_for_row(entity_id, pf)
        if field is None:
            continue
        refusal = gate_refusal(
            conn,
            entity_id=entity_id,
            entity_type=str(rd["entity_type"]),
            field=field,
            row=rd,
            state_provenance={},
            resolver=None,
            as_of=as_of,
            fail_closed_on_missing_head=False,
        )
        if refusal:
            continue
        flagged_foldable += 1
        stripped = notes.strip()
        if not stripped:
            reasonless += 1
        elif any(c.strip().startswith("predicate normalize:") for c in notes.split(";")):
            predicate_normalize += 1
        else:
            other += 1
        if "contradict" in notes.lower():
            contradict_like += 1

    return {
        "flagged_foldable": flagged_foldable,
        "f3_rows": f3_rows,
        "reasonless": reasonless,
        "predicate_normalize": predicate_normalize,
        "other": other,
        "contradict_like_flag_foldable": contradict_like,
        "contradiction_chunk_flagged": contradiction_chunk_flagged,
        "contradiction_chunk_contradiction": j_contradiction,
        "contradiction_chunk_cross_entity": j_cross,
        "contradiction_chunk_semantic": j_semantic,
    }
