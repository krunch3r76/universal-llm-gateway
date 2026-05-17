"""Predicate-form audit detectors — ledger-driven §8.2 finding kinds."""

from __future__ import annotations

from typing import Any

from ...db import query
from ._shared import _finding


def detect_unresolved_bare_token_in_predicate_form(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """§8.2.13 — assertions where normalization refused due to collision.

    Reads the normalization-decision ledger directly (no token scanning).
    Surfaces rows where the resolver enumerated >1 candidate and refused
    to bind, OR rows where no candidate matched but the raw form has the
    shape of a bare entity reference. NULL ledger rows (pre-v1.3.1) are
    skipped — they are out of scope for this finding kind.
    """
    sql = """
        SELECT id, entity_id, raw_predicate_form, normalization_decision,
               candidate_set_fingerprint, created_at
        FROM assertions
        WHERE normalization_decision IN ('collision_refused',
                                          'alias_collision_refused')
          AND superseded_by IS NULL
    """
    params: tuple = ()
    if subject:
        sql += " AND entity_id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "unresolved_bare_token_in_predicate_form",
            str(r["id"]),
            f"Assertion {r['id']} on {r['entity_id']}: predicate_form "
            f"{r['raw_predicate_form']!r} refused due to "
            f"{r['normalization_decision']} (candidate fingerprint "
            f"{r['candidate_set_fingerprint']}). Inspect and resolve via "
            "explicit prefix or supersede with disambiguated form.",
        )
        for r in rows
    ]


__all__ = ["detect_unresolved_bare_token_in_predicate_form"]
