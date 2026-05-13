"""Auditor-validatability detectors (Checks 4 & 5).

Operationalise Kaywan's principle (assertion 9715 on
``document:entity-backed-claim-provenance-v1``): whatever entity you
designate confirmed, an independent auditor (LLM) should be able to validate
it from the entity card alone. These run at ``session_close`` time scoped to
the session's entity_ids — not on every entity_create (too early; assertions
are written after the entity in typical session flow).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...db import query
from ._shared import _IDENT_SHAPED_VALUE_RE, _finding


def detect_confirmed_entity_no_assertions(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Entities at status='confirmed' with zero confirmed-confidence assertions.

    Check 4: an auditor reading the entity card cannot validate confirmed
    status when there are no confirmed assertions supporting it.
    """
    sql = """
        SELECT id, type, name FROM entities
        WHERE status = 'confirmed'
        AND type NOT IN ('todo', 'assertion')
        AND NOT EXISTS (
            SELECT 1 FROM assertions
            WHERE entity_id = entities.id
              AND confidence = 'confirmed'
              AND superseded_by IS NULL
        )
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "confirmed_entity_no_assertions",
            r["id"],
            f"{r['type']} '{r['name']}' is at status:confirmed but has zero assertions "
            f"at confidence:confirmed — auditor cannot validate confirmed status from entity "
            f"card alone. Seed a confirmed assertion citing the source. "
            f"See agent_skill:auditor-validatable-confidence.",
        )
        for r in rows
    ]


def detect_confirmed_attribute_no_assertion(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Confirmed entities with typed attributes that have no backing confirmed assertion.

    Check 5 (heuristic): for each attribute key/value on a confirmed entity,
    at least one confirmed assertion on that entity should reference the
    attribute (by key phrase or value substring). Auditors seeing a bare
    attribute value with no supporting assertion cannot verify it.

    Match heuristic: assertion claim contains the attribute key as a *whole
    word* (underscore→space normalised) — ``\\bdate\\b``, not ``candidate``.
    For values, substring match is only attempted when the value is
    identifier-shaped (alphanumerics + ``-._:/``) AND ≥4 chars — avoids
    matching free-text values or single tokens that incidentally co-occur.
    Heuristic; false positives are accepted; the tighter boundary scan
    removes the common false-negative suppressions (``date`` in ``candidate``,
    ``type`` in ``prototype``, …).
    """
    sql = """
        SELECT id, type, name, attributes FROM entities
        WHERE status = 'confirmed'
        AND attributes IS NOT NULL
        AND type NOT IN ('todo', 'assertion')
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings = []

    for r in rows:
        attrs_raw = r.get("attributes")
        if not attrs_raw:
            continue
        try:
            attrs = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
        except Exception:
            continue
        if not isinstance(attrs, dict) or not attrs:
            continue

        # Fetch all confirmed, non-superseded assertion claims for this entity.
        assertion_rows = query(
            conn,
            "SELECT claim FROM assertions "
            "WHERE entity_id = ? AND confidence = 'confirmed' AND superseded_by IS NULL",
            (r["id"],),
        )
        all_claims = " ".join(ar["claim"] or "" for ar in assertion_rows).lower()

        for attr_key, attr_val in attrs.items():
            key_normalised = attr_key.replace("_", " ").lower()
            val_str = str(attr_val).lower() if attr_val is not None else ""

            key_pattern = re.compile(r"\b" + re.escape(key_normalised) + r"\b")
            raw_key_pattern = re.compile(r"\b" + re.escape(attr_key.lower()) + r"\b")
            referenced = bool(
                key_pattern.search(all_claims) or raw_key_pattern.search(all_claims)
            )
            if (
                not referenced
                and len(val_str) >= 4
                and _IDENT_SHAPED_VALUE_RE.match(val_str)
            ):
                val_pattern = re.compile(r"\b" + re.escape(val_str) + r"\b")
                referenced = bool(val_pattern.search(all_claims))
            if not referenced:
                findings.append(
                    _finding(
                        "confirmed_attribute_no_assertion",
                        f"{r['id']}:{attr_key}",
                        f"Entity {r['id']} is at status:confirmed with typed attribute "
                        f"{attr_key}={attr_val!r} but no confirmed assertion appears to "
                        f"reference it — auditor sees an unsupported attribute. Seed a "
                        f"confirmed assertion citing the source for this attribute. "
                        f"See agent_skill:auditor-validatable-confidence.",
                    )
                )

    return findings


__all__ = [
    "detect_confirmed_attribute_no_assertion",
    "detect_confirmed_entity_no_assertions",
]
