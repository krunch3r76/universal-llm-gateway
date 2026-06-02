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

from ...confidence_field import confidence_band_sql_predicate, confidence_field
from ...db import query
from ._shared import _IDENT_SHAPED_VALUE_RE, _finding
from .substantiation import CONFIRMED, derive_substantiation_state


def detect_confirmed_entity_no_assertions(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Entities whose stored status='confirmed' does NOT derive from assertions.

    Fork D (G1, thread 1173): the confidence axis is DERIVED, not hand-set. This
    detector no longer fires merely because a status was hand-set to 'confirmed'
    — birth default is now 'unsubstantiated' and hand-set confidence-axis writes
    are frozen, so the false-positive class cannot regenerate. What remains is
    the legacy population: rows that still STORE status='confirmed' but whose
    derived substantiation state (from backing assertions) is not 'confirmed'.
    Those are the sweep's residual debt, surfaced here via the canonical
    derivation rather than an inline existence check.

    Check 4, Gate-0 aware: each type's auditable-confidence axis is declared in
    type_confidence_fields and read via confidence_field(). The finding fires
    only for `status`-axis types; `none`/`workflow_state` types never fire, and
    `content_hash` types fire only when the structural verifier is absent.
    """
    band_pred = confidence_band_sql_predicate()
    sql = f"""
        SELECT id, type, name, content_hash, status, confidence_band FROM entities
        WHERE {band_pred}
        AND type != 'assertion'
    """
    params: tuple = ("confirmed",)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)
    rows = query(conn, sql, params)
    findings: list[dict[str, Any]] = []
    for r in rows:
        # Derive substantiation from backing assertions; an entity whose stored
        # status claims confirmed but derives as confirmed is consistent and not
        # flagged. This replaces the prior inline NOT EXISTS check so full-D can
        # evolve the rule in one place.
        if derive_substantiation_state(conn, r["id"]) == CONFIRMED:
            continue
        cf = confidence_field(conn, r["type"])
        if cf in ("none", "workflow_state"):
            continue  # Gate 0: status is not this type's confidence axis.
        if cf == "content_hash" and r["content_hash"]:
            continue  # Gate 0: structural verifier satisfies the binding.
        findings.append(
            _finding(
                "confirmed_entity_no_assertions",
                r["id"],
                f"{r['type']} '{r['name']}' stores status:confirmed but its derived "
                f"substantiation (from backing assertions) is not 'confirmed' — auditor "
                f"cannot validate confirmed status from the entity card alone. Under Fork D "
                f"confidence is derived: seed a confirmed assertion citing the source (the "
                f"derived state then becomes 'confirmed'), or let the entity fall back to "
                f"'unsubstantiated'. See agent_skill:auditor-validatable-confidence.",
            )
        )
    return findings


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
    band_pred = confidence_band_sql_predicate()
    sql = f"""
        SELECT id, type, name, attributes, status, confidence_band FROM entities
        WHERE {band_pred}
        AND attributes IS NOT NULL
        AND type != 'assertion'
    """
    params: tuple = ("confirmed",)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)
    rows = query(conn, sql, params)
    findings = []

    for r in rows:
        cf = confidence_field(conn, r["type"])
        if cf in ("none", "workflow_state"):
            continue  # Gate 0: status is not this type's confidence axis.
        # content_hash + status types continue: a structural binding does NOT
        # cover INTERPRETED attributes ([policy:content-hash-scope], Q2), so
        # attribute findings still require a backing assertion.
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
