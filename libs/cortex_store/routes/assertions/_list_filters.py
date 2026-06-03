"""Shared WHERE-clause builders for assertion list endpoints."""

from __future__ import annotations


def _escape_like_literal(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_substring_pattern(substring: str) -> str:
    return f"%{_escape_like_literal(substring)}%"


def _like_prefix_pattern(prefix: str) -> str:
    return f"{_escape_like_literal(prefix)}%"


def append_assertion_list_filters(
    clauses: list[str],
    params: list[str | int],
    *,
    entity_id: str | None = None,
    entity_id_prefix: str | None = None,
    claim_filter: str | None = None,
    seeded_by: str | None = None,
    confidence: str | None = None,
    review_status: str | None = None,
    superseded: bool | None = None,
    valid_at: str | None = None,
    known_at: str | None = None,
    entity_type: str | None = None,
    entity_type_exclude: str | None = None,
) -> bool:
    """Append SQL filters on alias ``a``. Returns whether entities join is required."""
    needs_join = bool(entity_type or entity_type_exclude)

    if entity_id:
        clauses.append("a.entity_id = ?")
        params.append(entity_id)
    elif entity_id_prefix:
        prefix = entity_id_prefix.strip()
        if prefix:
            clauses.append("a.entity_id LIKE ? ESCAPE '\\'")
            params.append(_like_prefix_pattern(prefix))

    if claim_filter:
        stripped = claim_filter.strip()
        if stripped:
            clauses.append("a.claim LIKE ? ESCAPE '\\'")
            params.append(_like_substring_pattern(stripped))

    if seeded_by:
        clauses.append("a.seeded_by = ?")
        params.append(seeded_by)

    if confidence:
        clauses.append("a.confidence = ?")
        params.append(confidence)
    if review_status:
        clauses.append("a.review_status = ?")
        params.append(review_status)
    if superseded is False:
        clauses.append("a.superseded_by IS NULL")
    elif superseded is True:
        clauses.append("a.superseded_by IS NOT NULL")

    if entity_type:
        clauses.append("e.type = ?")
        params.append(entity_type)
        needs_join = True
    if entity_type_exclude:
        excluded = [t.strip() for t in entity_type_exclude.split(",") if t.strip()]
        placeholders = ",".join("?" for _ in excluded)
        clauses.append(f"e.type NOT IN ({placeholders})")
        params.extend(excluded)
        needs_join = True

    if valid_at:
        clauses.append("(a.valid_from IS NULL OR a.valid_from <= ?)")
        params.append(valid_at)
        clauses.append("(a.valid_until IS NULL OR a.valid_until > ?)")
        params.append(valid_at)
        clauses.append("a.superseded_by IS NULL")
    elif known_at:
        clauses.append("a.created_at <= ?")
        params.append(known_at)

    return needs_join


__all__ = ["append_assertion_list_filters"]
