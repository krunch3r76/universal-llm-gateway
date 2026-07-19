"""Load prior digest assertions tagged to a journal entry anchor."""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import json_decode, query

_ACTIVE = "superseded_by IS NULL"


def load_active_assertions_for_anchor(
    conn: sqlite3.Connection,
    entry_anchor: str,
) -> list[dict[str, Any]]:
    """Return ACTIVE assertions emitted by digest for *entry_anchor*.

    Loader substrate (OG2/G6): ``reasoning_summary`` prefix ``digest:{anchor}#``
    written by initial digest EMIT; ``evidence_uris`` anchor fragment is secondary.
    """
    prefix = f"digest:{entry_anchor}#"
    rows = query(
        conn,
        "SELECT id, entity_id, claim, confidence, derivation_type, evidence, "
        "evidence_uris, reasoning_summary, valid_from, confidence_score "
        f"FROM assertions WHERE {_ACTIVE} AND reasoning_summary LIKE ? "
        "ORDER BY id ASC",
        (f"{prefix}%",),
    )
    if rows:
        return [_decode_assertion(row) for row in rows]

    fragment = f"#{entry_anchor.split('#', 1)[-1]}"
    rows = query(
        conn,
        f"SELECT id, entity_id, claim, confidence, derivation_type, evidence, "
        "evidence_uris, reasoning_summary, valid_from, confidence_score "
        f"FROM assertions WHERE {_ACTIVE} AND evidence_uris LIKE ? "
        "ORDER BY id ASC",
        (f"%{fragment}%",),
    )
    return [_decode_assertion(row) for row in rows]


def _decode_assertion(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["evidence_uris"] = json_decode(out.get("evidence_uris"), fallback=[])
    return out


__all__ = ["load_active_assertions_for_anchor"]
