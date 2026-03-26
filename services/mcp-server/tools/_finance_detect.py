"""Statement type auto-detection via keyword scoring.

Uses weighted keyword scoring on extracted text to classify statement type.
Claude API fallback only when the top score is ambiguous (margin < threshold).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.3

_TYPE_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "mortgage": [
        ("mortgage", 3.0),
        ("principal balance", 2.5),
        ("escrow", 1.5),
        ("loan number", 2.0),
        ("maturity date", 2.0),
        ("property address", 1.5),
        ("monthly payment", 1.0),
        ("interest rate", 1.0),
        ("unpaid principal", 2.5),
        ("loan-to-value", 2.0),
        ("home lending", 2.0),
    ],
    "escrow": [
        ("escrow account", 3.0),
        ("escrow analysis", 3.0),
        ("escrow disclosure", 3.0),
        ("escrow balance", 2.5),
        ("shortage", 2.0),
        ("surplus", 2.0),
        ("cushion", 2.0),
        ("projected disbursement", 2.5),
        ("escrow payment", 2.0),
        ("property tax", 1.0),
        ("homeowners insurance", 1.0),
    ],
    "credit_card": [
        ("credit card", 3.0),
        ("credit limit", 2.5),
        ("minimum payment due", 2.0),
        ("annual percentage rate", 2.0),
        ("apr", 1.5),
        ("cash advance", 1.5),
        ("balance transfer", 1.5),
        ("new balance", 1.5),
        ("purchases", 1.0),
        ("rewards", 1.0),
    ],
    "checking": [
        ("checking account", 3.0),
        ("opening balance", 2.5),
        ("closing balance", 2.5),
        ("deposits and additions", 2.0),
        ("withdrawals", 1.5),
        ("check number", 2.0),
        ("overdraft", 1.5),
        ("direct deposit", 1.0),
    ],
    "utility": [
        ("electric", 2.0),
        ("gas service", 2.0),
        ("kwh", 2.5),
        ("therms", 2.5),
        ("meter reading", 2.0),
        ("utility", 1.5),
        ("energy statement", 2.0),
        ("baseline allowance", 2.0),
        ("tiered rate", 1.5),
    ],
    "phone": [
        ("wireless", 2.0),
        ("mobile", 1.5),
        ("phone number", 1.5),
        ("data plan", 2.0),
        ("talk & text", 2.0),
        ("roaming", 1.5),
        ("device payment", 2.0),
        ("line access", 2.0),
    ],
    "ploc": [
        ("personal line of credit", 3.0),
        ("line of credit", 2.5),
        ("credit line", 2.0),
        ("available credit", 1.5),
        ("advance", 1.5),
        ("credit limit", 1.0),
    ],
    "student_loan": [
        ("student loan", 3.0),
        ("federal loan", 2.5),
        ("subsidized", 2.0),
        ("unsubsidized", 2.0),
        ("repayment plan", 2.0),
        ("loan servicer", 2.0),
        ("deferment", 1.5),
        ("forbearance", 1.5),
        ("fafsa", 2.0),
    ],
    "brokerage": [
        ("brokerage", 3.0),
        ("portfolio", 2.0),
        ("holdings", 2.0),
        ("market value", 2.0),
        ("unrealized gain", 2.5),
        ("cost basis", 2.5),
        ("dividend", 1.5),
        ("securities", 1.5),
        ("shares", 1.0),
        ("etf", 1.0),
    ],
    "tax_document": [
        ("1099", 3.0),
        ("w-2", 3.0),
        ("1098", 2.5),
        ("1095", 2.5),
        ("tax year", 2.0),
        ("federal tax", 1.5),
        ("form type", 1.5),
        ("payer's", 1.5),
        ("recipient's tin", 2.0),
    ],
    "property_tax": [
        ("property tax", 3.0),
        ("parcel number", 2.5),
        ("assessed value", 2.5),
        ("tax rate area", 2.0),
        ("installment", 1.5),
        ("secured tax", 2.0),
        ("supplemental tax", 2.0),
        ("assessor", 2.0),
    ],
}


def score_statement_type(text: str) -> list[tuple[str, float]]:
    """Score each statement type by keyword matches in extracted text.

    Returns a list of (type, score) tuples sorted by score descending.
    """
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for stype, keywords in _TYPE_KEYWORDS.items():
        total = 0.0
        for keyword, weight in keywords:
            if keyword in text_lower:
                total += weight
        if total > 0:
            scores[stype] = total
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def detect_statement_type(
    extraction: dict[str, Any],
) -> tuple[str | None, float, list[tuple[str, float]]]:
    """Detect statement type from extracted PDF content.

    Returns (detected_type, confidence_margin, all_scores).
    detected_type is None if no keywords matched.
    confidence_margin is the gap between #1 and #2 scores (normalized).
    """
    all_text = "\n".join(p.get("text", "") for p in extraction.get("pages", []))
    scores = score_statement_type(all_text)

    if not scores:
        return None, 0.0, scores

    top_type, top_score = scores[0]
    if len(scores) < 2:
        return top_type, 1.0, scores

    second_score = scores[1][1]
    margin = (top_score - second_score) / top_score if top_score > 0 else 0.0
    return top_type, margin, scores


def is_confident(margin: float) -> bool:
    """Whether the detection margin is above the confidence threshold."""
    return margin >= _CONFIDENCE_THRESHOLD
