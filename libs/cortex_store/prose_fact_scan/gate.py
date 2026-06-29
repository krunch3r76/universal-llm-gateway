"""Active assertion eligibility gate."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .constants import ELIGIBLE_CONFIDENCE, ELIGIBLE_REVIEW_STATUS


def _parse_day(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[: len(fmt.replace("%", "0"))], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def is_temporally_active(row: dict[str, Any], today: date | None = None) -> bool:
    if row.get("superseded_by") is not None:
        return False
    day = today or date.today()
    valid_until = _parse_day(row.get("valid_until"))
    if valid_until is not None and valid_until <= day:
        return False
    return True


def is_gate_eligible(row: dict[str, Any]) -> bool:
    confidence = row.get("confidence")
    review_status = row.get("review_status")
    return (
        confidence in ELIGIBLE_CONFIDENCE
        and review_status in ELIGIBLE_REVIEW_STATUS
    )


def filter_active_eligible(
    rows: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if is_temporally_active(row, today) and is_gate_eligible(row)
    ]
