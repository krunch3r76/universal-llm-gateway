"""Houseless tab-seat wake association guard (Packet 2 E2).

A houseless seat whose Jupiter row is ``status: tab`` is woken only when the waking
thread's current bus association (``get_current_cse``, append-only) is
``associated`` and names the same registration and the same chat
(``holder_id_from_chat_url`` on both). Refusals use ledger reason
``houseless_association_mismatch`` via the dispatcher's existing no-route marker.
"""

from __future__ import annotations

import os
from typing import Any

from claude_bundles.holder_strings import holder_id_from_chat_url
from universal_logging import get_logger

logger = get_logger(__name__)

HOUSELESS_TAB_KEY = "houseless_tab"
ASSOCIATION_MISMATCH = "houseless_association_mismatch"

__all__ = [
    "ASSOCIATION_MISMATCH",
    "HOUSELESS_TAB_KEY",
    "guard_houseless_tab",
    "houseless_tab_refusal",
]


def houseless_tab_refusal(holder: Any, wake_thread: str | None) -> str | None:
    if not isinstance(holder, dict) or not holder.get(HOUSELESS_TAB_KEY):
        return None
    thread = str(wake_thread or "").strip()
    if not thread or not os.environ.get("AGENT_BUS_DB_PATH"):
        return ASSOCIATION_MISMATCH
    try:
        from agent_bus_store.db.cse_associations import get_current_cse

        row = get_current_cse(thread_id=thread)
    except LookupError:
        return ASSOCIATION_MISMATCH
    except Exception:  # noqa: BLE001 — an unreadable association refuses
        logger.warning(
            "houseless tab guard: association read failed thread=%s",
            thread,
            exc_info=True,
        )
        return ASSOCIATION_MISMATCH
    if str(row.get("state") or "") != "associated":
        return ASSOCIATION_MISMATCH
    reg = str(holder.get("registration_id") or "").strip()
    if str(row.get("cse_registration_id") or "").strip() != reg:
        return ASSOCIATION_MISMATCH
    want = holder_id_from_chat_url(str(holder.get("chat_url") or ""))
    have = holder_id_from_chat_url(str(row.get("cse_chat_url") or ""))
    if not want or want != have:
        return ASSOCIATION_MISMATCH
    return None


def guard_houseless_tab(
    holder: dict[str, Any] | None, wake_thread: str | None, *, no_route_key: str
) -> dict[str, Any] | None:
    reason = houseless_tab_refusal(holder, wake_thread)
    if reason is None:
        return holder
    return {no_route_key: reason}
