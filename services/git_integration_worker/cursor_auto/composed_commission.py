"""Nest park-membership composition label for Auto CLOSEOUT envelopes.

``composed_commission:`` is orthogonal to envelope ``status:`` — a parent may
report protocol-complete while immediate nest children failed or never finished.
Fleet readers grep this line without opening sidecars or inferring from silence.
"""

from __future__ import annotations

from typing import Any, Protocol

from universal_logging import get_logger

logger = get_logger(__name__)

# Frozen enum (a:28834 / G2 frame) — sole spellings; silence forbidden.
COMPOSED_COMMISSION_NA = "n/a — not-in-closure"
COMPOSED_COMMISSION_FAILED = "failed"
COMPOSED_COMMISSION_INCOMPLETE = "incomplete"
COMPOSED_COMMISSION_COMPLETE = "complete"

_LEDGER_CHILD_STATUSES = frozenset(
    {
        "queued",
        "admitted",
        "running",
        "parked_waiting",
        "completed",
        "failed",
        "cancelled",
    }
)
_NON_TERMINAL = frozenset({"queued", "admitted", "running", "parked_waiting"})
_TERMINAL_FAILURE = frozenset({"failed", "cancelled"})


class _CompositionLedger(Protocol):
    def list_nested_children(self, *, parent_dispatch_id: str) -> list[str]: ...

    def dispatch_status_by_id(self, *, dispatch_id: str) -> dict[str, Any] | None: ...


def resolve_composition_parent_id(
    *,
    closing_dispatch_id: str,
    nest_under: str | None,
) -> str:
    """Return the park parent key for downward aggregation.

    Nest-park Auto closeouts stamp ``meta.nest_under=P`` while the closing row
    is the child dispatch — aggregate under *P*, not the closing id alone.
    """
    if nest_under:
        return nest_under
    return closing_dispatch_id


def prose_composed_commission_line(value: str) -> str:
    """Format the always-present envelope header line for nest composition honesty."""
    return f"composed_commission: {value}"


def compute_composed_commission(
    *,
    parent_dispatch_id: str,
    ledger: _CompositionLedger,
) -> str:
    """Map immediate nest children to one frozen ``composed_commission`` value.

    Precedence: empty→N/A; any failed/cancelled→failed; else any
    non-terminal/unknown/None→incomplete; else all completed→complete.
    Grandchildren are ignored (single-depth only).
    """
    try:
        child_ids = ledger.list_nested_children(parent_dispatch_id=parent_dispatch_id)
    except Exception as exc:
        logger.warning(
            "composed_commission list_nested_children failed parent=%s: %s",
            parent_dispatch_id,
            exc,
        )
        return COMPOSED_COMMISSION_INCOMPLETE

    if not child_ids:
        return COMPOSED_COMMISSION_NA

    has_failed = False
    has_incomplete = False
    all_completed = True

    for child_id in child_ids:
        try:
            row = ledger.dispatch_status_by_id(dispatch_id=child_id)
        except Exception as exc:
            logger.warning(
                "composed_commission dispatch_status_by_id failed child=%s: %s",
                child_id,
                exc,
            )
            return COMPOSED_COMMISSION_INCOMPLETE

        status = row.get("status") if row else None

        if status is None or status not in _LEDGER_CHILD_STATUSES:
            logger.warning(
                "composed_commission unknown child status child=%s status=%r",
                child_id,
                status,
            )
            has_incomplete = True
            all_completed = False
            continue

        if status in _TERMINAL_FAILURE:
            has_failed = True
            all_completed = False
        elif status in _NON_TERMINAL:
            has_incomplete = True
            all_completed = False
        elif status != "completed":
            has_incomplete = True
            all_completed = False

    if has_failed:
        return COMPOSED_COMMISSION_FAILED
    if has_incomplete:
        return COMPOSED_COMMISSION_INCOMPLETE
    if all_completed:
        return COMPOSED_COMMISSION_COMPLETE
    return COMPOSED_COMMISSION_INCOMPLETE
