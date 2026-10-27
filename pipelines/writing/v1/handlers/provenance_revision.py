"""Revision delta between draft v1 and v2 (dropped names/numbers, reorder)."""

from __future__ import annotations

from typing import Any

from writing_intake.claim_gates import check_class_for_channel

from .email_envelope import normalize_contractions
from .provenance_draft import (
    _NAME,
    _NAME_STOP,
    _NUMBER,
    _parse_draft,
)
from .provenance_envelope import _is_non_claim_envelope_unit


def _violation(kind: str, claim_id: str | None, detail: Any) -> dict[str, Any]:
    return {"type": kind, "claim_id": claim_id, "detail": detail}


def _pin_order(clauses: list[dict[str, Any]]) -> list[str]:
    order: list[str] = []
    for clause in clauses:
        pin_ids = clause.get("pin_ids") if isinstance(clause.get("pin_ids"), list) else []
        for pin_id in pin_ids:
            key = str(pin_id)
            if key not in order:
                order.append(key)
    return order


def revision_delta(
    draft_v1: Any,
    draft_v2: Any,
    *,
    v1_clauses: list[dict[str, Any]] | None = None,
    v2_clauses: list[dict[str, Any]] | None = None,
    pin_ledger: list[dict[str, Any]] | None = None,
    channel: str | None = None,
    signer: str | None = None,
) -> list[dict[str, Any]]:
    """Literal dropped-name/number checks and fact_reordered from judge clause order."""
    del pin_ledger
    prior = _parse_draft(draft_v1)
    current = _parse_draft(draft_v2)
    if prior is None or current is None:
        return []
    v1_text = prior["draft"]
    v2_text = current["draft"]
    v1_name_text = normalize_contractions(v1_text)
    v2_name_text_lower = normalize_contractions(v2_text).lower()
    v1_clause_rows = v1_clauses or []
    v2_clause_rows = v2_clauses or []
    violations: list[dict[str, Any]] = []
    v1_order = _pin_order(v1_clause_rows)
    v2_order = _pin_order(v2_clause_rows)
    if v1_order and v2_order and v1_order != v2_order:
        profile = check_class_for_channel(str(channel or "filing"))
        if profile == "sworn":
            violations.append(
                _violation("fact_reordered", None, {"v1": v1_order, "v2": v2_order})
            )
    for name in _NAME.findall(v1_name_text):
        if name in _NAME_STOP:
            continue
        if name.lower() not in v2_name_text_lower:
            violations.append(_violation("v2_dropped_name", None, name))
            violations.append(_violation("fact_dropped", None, name))
    for number in _NUMBER.findall(v1_text):
        if number not in v2_text:
            violations.append(_violation("v2_dropped_number", None, number))
            violations.append(_violation("fact_dropped", None, number))
    for row in v2_clause_rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if (
            row.get("attested") is False
            and text
            and not _is_non_claim_envelope_unit(text, channel=channel, signer=signer)
        ):
            violations.append(
                _violation(
                    "added_unattested_fact",
                    None,
                    text[:120],
                )
            )
    return violations
