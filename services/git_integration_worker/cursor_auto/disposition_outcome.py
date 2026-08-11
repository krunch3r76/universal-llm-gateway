"""Path-gate reader-facing disposition outcome tokens (arc 6655).

``disposition_hint`` remains policy routing inside contract_info. Reader-facing
``disposition`` may carry the outcome token ``dispatched-and-relayed`` only when
M1 holds at the stamp site (nested relay success with dispatch_id, or CDP
commission with execution_id). Callers omit the field when this module returns
None — silence is the required alternate, not a new enum value.
"""

from __future__ import annotations

_OUTCOME_DISPATCHED = "dispatched-and-relayed"


def m1_nested_relay(*, dispatch_id: str | None, relay_ok: bool) -> bool:
    """True when nested closeout relay co-observes a dispatch identity and success."""
    return bool(dispatch_id) and bool(relay_ok)


def m1_cdp_commission(*, execution_id: str | None) -> bool:
    """True when a CDP commission payload carries a non-empty execution_id."""
    return bool(execution_id)


def outcome_disposition_for_stamp(
    disposition_hint: str,
    *,
    m1_satisfied: bool,
) -> str | None:
    """Return reader-facing disposition, or None when the outcome token must omit.

    Non-outcome policy labels (answered, conferred, executed, …) pass through
    regardless of M1. The false-outcome token ``dispatched-and-relayed`` returns
    only when *m1_satisfied* is true.
    """
    hint = str(disposition_hint or "").strip()
    if hint == _OUTCOME_DISPATCHED:
        return _OUTCOME_DISPATCHED if m1_satisfied else None
    return hint or None
