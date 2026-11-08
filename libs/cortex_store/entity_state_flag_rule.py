"""Flag withhold vs fold-with-marker rule (W4 decision 5)."""

from __future__ import annotations

from typing import Any, Final

from cortex_store.card import _extract_flag_reason

CONTRADICTION_PREFIXES: Final[tuple[str, ...]] = (
    "contradiction:",
    "Cross-entity contradiction detected:",
    "Semantic contradiction:",
)


def _chunks(notes: str | None) -> list[str]:
    if not notes:
        return []
    return [part.strip() for part in notes.split(";")]


def _chunk_has_contradiction_prefix(chunk: str) -> bool:
    lower = chunk.lower()
    for prefix in CONTRADICTION_PREFIXES:
        if lower.startswith(prefix.lower()):
            return True
    return False


def flag_withholds(
    review_status: str | None, review_notes: str | None, row: dict[str, Any]
) -> bool:
    """True when decision 5 withholds authority (contradiction chunk or reviewed notes)."""
    if review_status != "flagged":
        return False
    notes_raw = str(review_notes or "")
    stripped = notes_raw.strip()
    for chunk in _chunks(notes_raw):
        if _chunk_has_contradiction_prefix(chunk):
            return True
    if not stripped:
        return False
    for chunk in _chunks(notes_raw):
        if chunk.startswith("predicate normalize:"):
            return False
    reviewer = row.get("reviewer")
    reviewed_at = row.get("reviewed_at")
    if reviewer is not None or reviewed_at is not None:
        return True
    return False


def flag_fold_marker(
    review_status: str | None, review_notes: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    """Marker and flag_detail for foldable flagged claims (decision 5)."""
    if review_status != "flagged":
        return None, None
    notes_raw = str(review_notes or "")
    token = _extract_flag_reason(notes_raw)
    return "flagged, no reason", {"notes": review_notes, "token": token}
