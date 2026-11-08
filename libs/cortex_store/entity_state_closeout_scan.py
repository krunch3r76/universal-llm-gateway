"""F3 closeout scan for host runs (not imported by fold hot path)."""

from __future__ import annotations

import sqlite3

from cortex_store.card_temporal import card_as_of_utc, install_card_temporal_function
from cortex_store.drift_detector import scan_entity_state_drift
from cortex_store.entity_state_flag_rule import flag_withholds
from cortex_store.entity_state_fold_gate import gate_refusal, parse_status_current


def _f3_newer_assertion_ids(conn: sqlite3.Connection) -> set[int]:
    ids: set[int] = set()
    for row in scan_entity_state_drift(conn=conn).rows:
        if row.drift_class != "F3":
            continue
        cite = row.right_cite or ""
        if cite.startswith("assertion:"):
            try:
                ids.add(int(cite.split(":", 1)[1]))
            except ValueError:
                continue
    return ids


def scan_live_w4_flagged_closeout_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Read-only F3 closeout counters (``flagged_foldable`` ⊆ F3 newer claims)."""
    install_card_temporal_function(conn)
    as_of = card_as_of_utc()
    f3_newer = _f3_newer_assertion_ids(conn)
    f3_rows = sum(1 for r in scan_entity_state_drift(conn=conn).rows if r.drift_class == "F3")
    reasonless = predicate_normalize = other = 0
    flagged_foldable = 0
    contradict_like = 0
    j_contradiction = j_cross = j_semantic = 0
    contradiction_chunk_flagged = 0

    for row in conn.execute(
        "SELECT review_notes FROM assertions WHERE review_status = 'flagged'"
    ):
        notes = str(row[0] or "")
        for chunk in [c.strip() for c in notes.split(";") if c.strip()]:
            lower = chunk.lower()
            if lower.startswith("contradiction:"):
                j_contradiction += 1
                contradiction_chunk_flagged += 1
                break
            if lower.startswith("cross-entity contradiction detected:"):
                j_cross += 1
                contradiction_chunk_flagged += 1
                break
            if lower.startswith("semantic contradiction:"):
                j_semantic += 1
                contradiction_chunk_flagged += 1
                break

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
        if parse_status_current(str(rd.get("predicate_form") or "")) is None:
            continue
        refusal = gate_refusal(
            conn,
            entity_id=str(rd["entity_id"]),
            entity_type=str(rd["entity_type"]),
            field="application_submitted",
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
