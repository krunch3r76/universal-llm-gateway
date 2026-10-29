"""Deterministic scrub of not_purpose_relevant drift after the producer runs."""

from __future__ import annotations

import re
from typing import Any

from writing_intake.claim_gates import (
    map_claim_to_purpose,
    parse_purpose_points,
    resolve_claim_provenance,
)


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def sentence_restates_purpose_point(
    sentence: str,
    purpose_points: list[str],
    *,
    pin_ledger: list[dict[str, Any]],
) -> bool:
    """True when the sentence carries a stated purpose point (incl. contact digits)."""
    if not purpose_points:
        return False
    parsed = parse_purpose_points("", purpose_points)
    if map_claim_to_purpose(sentence, parsed) is not None:
        return True
    sent_lower = sentence.lower()
    sent_digits = _digits_only(sentence)
    for point in purpose_points:
        text = str(point).strip()
        if not text:
            continue
        point_lower = text.lower()
        if point_lower in sent_lower or sent_lower in point_lower:
            return True
        point_digits = _digits_only(text)
        if len(point_digits) >= 7 and point_digits in sent_digits:
            return True
        if len(sent_digits) >= 10 and re.search(
            r"\b(?:phone|number|reach|call|reply|contact)\b", text, re.I
        ):
            for pin in pin_ledger:
                if not isinstance(pin, dict):
                    continue
                excerpt_digits = _digits_only(str(pin.get("excerpt") or ""))
                if excerpt_digits and sent_digits in excerpt_digits:
                    return True
    return False


def scrub_purpose_restatement_flags(
    drift: dict[str, Any],
    purpose_points: list[str],
    *,
    pin_ledger: list[dict[str, Any]],
    purpose_text: str,
    verbatim_notes: list[str],
) -> dict[str, Any]:
    """Drop not_purpose_relevant flags when the sentence restates purpose context."""
    if not purpose_points and not purpose_text.strip():
        return drift
    flagged = drift.get("flagged_sentences") or []
    if not isinstance(flagged, list):
        return drift
    kept: list[Any] = []
    objective = purpose_text.strip()
    notes = [str(note) for note in verbatim_notes if str(note).strip()]
    for row in flagged:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        if row.get("reason") != "not_purpose_relevant":
            kept.append(row)
            continue
        sentence = str(row.get("sentence") or "")
        drop = False
        if sentence_restates_purpose_point(
            sentence, purpose_points, pin_ledger=pin_ledger
        ):
            drop = True
        elif objective and map_claim_to_purpose(sentence, [objective]) is not None:
            drop = True
        elif (
            resolve_claim_provenance(
                sentence,
                verbatim_notes=notes,
                attested_sources=[],
                intake_answers=[],
                fact_sources=[],
                strict_attestation=False,
                purpose_points=[],
            )
            == "operator_account"
        ):
            drop = True
        if drop:
            continue
        kept.append(row)
    out = dict(drift)
    out["flagged_sentences"] = kept
    out["passed"] = not kept
    return out
