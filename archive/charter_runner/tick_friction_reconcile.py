"""Tick-scan friction reconcile — mint follow-ons without waiting on window close.

Friction a:26603: ``reconcile_charter_frictions`` previously ran only from
``after_window_terminal_harvested``. Roots whose gated lane is terminal never
close another window, so actionable frictions stamped to those roots stay
unminted forever. Option (a): run the same idempotent reconcile for every
enrolled root on each tick scan.
"""

from __future__ import annotations

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


async def reconcile_enrolled_roots_on_tick(
    roots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Mint missing follow-ons for each enrolled root.

    Returns the flat list of ``{todo_id, assertion_id}`` dicts minted this pass.
    Failures are logged and do not abort the tick.
    """
    from cortex_store.dispatch_ops._friction_enqueue import reconcile_charter_frictions

    minted_all: list[dict[str, Any]] = []
    for thread in roots:
        root_id = str(thread.get("id") or "")
        if not root_id:
            continue
        try:
            minted = reconcile_charter_frictions(root_id)
        except Exception:  # noqa: BLE001 — tick must continue
            logger.exception(
                "tick-scan friction reconcile failed root=%s", root_id
            )
            continue
        if minted:
            minted_all.extend(minted)
    return minted_all


__all__ = ["reconcile_enrolled_roots_on_tick"]
