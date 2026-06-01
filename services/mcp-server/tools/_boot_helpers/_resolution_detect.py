"""Stale open_items tagging for boot — delegates to shared reconciliation.

The matching logic lives in ``cortex_store.open_items.reconcile`` so boot
(here) and the control tower aggregation (cortex-api) run identical
reconciliation. This module adapts the per-session boot shape: it tags
matched items with ``[RESOLVED]`` (boot keeps them for audit) rather than
omitting them.
"""

from __future__ import annotations

from typing import Any

from cortex_store.open_items.reconcile import (
    build_resolution_index,
    reconcile_open_items,
)


def filter_stale_open_items(
    sessions: list[dict[str, Any]],
    recently_resolved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Tag open_items that reference recently-resolved work.

    ``recently_resolved`` is the unified resolved-record set from
    ``/boot-temporal`` (superseded assertions + closed todos). Matched items
    receive a ``[RESOLVED]`` prefix so they no longer read as actionable while
    preserving an audit trail. Detection strategies (ref tag, todo slug,
    phrase, token overlap) are defined in the shared module.
    """
    if not recently_resolved:
        return sessions

    index = build_resolution_index(recently_resolved)
    if index.is_empty():
        return sessions

    result: list[dict[str, Any]] = []
    for session in sessions:
        open_items = session.get("open_items") or []
        reconciled = reconcile_open_items(open_items, index=index, omit_resolved=False)
        result.append({**session, "open_items": reconciled})
    return result
