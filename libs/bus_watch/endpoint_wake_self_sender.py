"""Scan-time self-wake guard and suppression tombstones for the endpoint wake ledger.

A tab often holds several mailboxes on one thread (seat name, house alias, bare
``cursor``). A turn it posts to any of them must not wake it: the wake lands as
a new user turn while the sender is mid-turn and starts a parallel run.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_seat.registry import normalize_bus_address

logger = logging.getLogger(__name__)

SELF_SENDER_RULE = "drop_self_sender"
_TAB_SURFACES = frozenset({"cursor", "grok"})
# Shared mailbox names: holding one never proves which tab posted.
_SHARED_SENDER_NAMES = frozenset({"", "cursor", "all", "team"})
_TAB_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def _tab_agent_id(sub: dict[str, Any]) -> str:
    if str(sub.get("surface") or "") not in _TAB_SURFACES:
        return ""
    return str(sub.get("agent_id") or "").strip().lower()


def sender_tab_ids(store: Any, from_agent: str, scopes: set[str]) -> set[str]:
    """Tab ``agent_id`` values the sender address resolves to within ``scopes``.

    The address itself counts when it is a tab uuid; otherwise every subscribed
    display row held under that seat name on one of ``scopes`` (or ``*``).
    Duck-typed stores without ``subscriptions`` resolve by uuid only.
    """
    raw = str(from_agent or "").strip()
    ids: set[str] = set()
    if _TAB_UUID_RE.match(raw.lower()):
        ids.add(raw.lower())
    if normalize_bus_address(raw) in _SHARED_SENDER_NAMES:
        return ids
    lookup = getattr(store, "subscriptions", None)
    if lookup is None:
        return ids
    try:
        rows = lookup(raw)
    except Exception:
        logger.exception("endpoint_wake_self_sender: sender lookup failed from=%s", raw)
        return ids
    for row in rows or []:
        if str(row.get("state") or "") != "subscribed":
            continue
        if str(row.get("thread_scope") or "") not in scopes | {"*"}:
            continue
        aid = _tab_agent_id(row)
        if aid:
            ids.add(aid)
    return ids


def is_self_wake(
    store: Any,
    turn: dict[str, Any],
    resolved: tuple[str, dict[str, Any]] | None,
) -> bool:
    """True when the resolved recipient tab is a tab the sender itself holds."""
    if resolved is None:
        return False
    sub = resolved[1]
    target = _tab_agent_id(sub)
    if not target:
        return False
    thread = str(turn.get("thread") or "")
    scopes = {thread, str(sub.get("thread_scope") or thread)}
    return target in sender_tab_ids(store, str(turn.get("from_agent") or ""), scopes)


def suppressed_item(item: dict[str, Any], rule_id: str) -> dict[str, Any]:
    """Ledger tombstone for a turn the scan decided not to wake."""
    return {
        **item,
        "decided_by_rule": rule_id,
        "state": "skipped",
        "reason": f"suppressed:{rule_id}",
    }
