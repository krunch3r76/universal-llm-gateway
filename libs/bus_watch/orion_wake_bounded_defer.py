"""Shared bounded wall-clock defer reasons for Orion chatgpt_work wakes (claim + receipt)."""

from __future__ import annotations

# Single source for SQL ``IN (...)`` (exact) and ``orion_bounded_defer_reason`` (exact + prefix).
# Prefix ``composer_not_focused`` covers ``composer_not_focused_other_control``, etc.
_ORION_BOUNDED_DEFER_REASONS = frozenset(
    {
        "non_empty_composer",
        "composer_not_empty",
        "composer_busy",
        "active_chat_busy",
        "composer_selection_active",
        "composer_not_focused",
        "composer_not_focused_other_control",
        "composer_not_focused_or_editable",
        "operator_active",
        "recent_operator_input",
        "conversation_mismatch",
        "no_composer",
        "cdp_transport_error",
    }
)


def orion_bounded_defer_reason(reason: str | None) -> bool:
    r = str(reason or "").strip()
    if r in _ORION_BOUNDED_DEFER_REASONS:
        return True
    return r.startswith("composer_not_focused")


def orion_bounded_defer_reasons_sql_sorted() -> tuple[str, ...]:
    """Stable order for SQLite ``IN`` placeholders (must match claim query params)."""
    return tuple(sorted(_ORION_BOUNDED_DEFER_REASONS))


__all__ = [
    "orion_bounded_defer_reason",
    "orion_bounded_defer_reasons_sql_sorted",
]
