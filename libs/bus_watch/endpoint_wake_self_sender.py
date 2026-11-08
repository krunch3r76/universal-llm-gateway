"""Scan-time self-wake guard and suppression tombstones for the endpoint wake ledger.

A tab often holds several mailboxes on one thread (seat name, house alias, bare
``cursor``). A turn it posts to any of them must not wake it: the wake lands as
a new user turn while the sender is mid-turn and starts a parallel run.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agent_bus_store.turns_models import TAB_UUID_RE
from agent_seat.registry import normalize_bus_address

logger = logging.getLogger(__name__)

SELF_SENDER_RULE = "drop_self_sender"
SELF_SENDER_RECENCY_RULE = "drop_self_sender_recency"
_TAB_SURFACES = frozenset({"cursor", "grok"})
# Shared mailbox names: holding one never proves which tab posted.
_SHARED_SENDER_NAMES = frozenset({"", "cursor", "all", "team"})
ENDPOINT_WAKE_SELF_RECENCY_S = int(os.environ.get("ENDPOINT_WAKE_SELF_RECENCY_S", "90"))


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
    if TAB_UUID_RE.match(raw.lower()):
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


def _recent_deliveries(
    store: Any,
    thread: str,
    window_s: int,
    before: str,
) -> list[str]:
    cache_holder = getattr(store, "_self_wake_recent_cache", None)
    cache_key = (thread, before, window_s)
    if isinstance(cache_holder, dict) and cache_key in cache_holder:
        return list(cache_holder[cache_key])
    recent = getattr(store, "recent_deliveries", None)
    if recent is None:
        out: list[str] = []
    else:
        try:
            out = [
                str(x).strip().lower()
                for x in (recent(thread, window_s, before=before) or [])
                if x
            ]
        except Exception:
            logger.exception(
                "endpoint_wake_self_sender: recent_deliveries failed thread=%s", thread
            )
            out = []
    if isinstance(cache_holder, dict):
        cache_holder[cache_key] = out
    return out


def is_self_wake(
    store: Any,
    turn: dict[str, Any],
    resolved: tuple[str, dict[str, Any]] | None,
) -> str | None:
    """Rule id when the resolved recipient tab is the sender's own tab, else None."""
    if resolved is None:
        return None
    sub = resolved[1]
    target = _tab_agent_id(sub)
    if not target:
        return None
    poster = str(turn.get("from_agent_id") or "").strip().lower()
    if poster and poster == target:
        return SELF_SENDER_RULE
    thread = str(turn.get("thread") or "")
    scopes = {thread, str(sub.get("thread_scope") or thread)}
    from_agent = str(turn.get("from_agent") or "")
    if target in sender_tab_ids(store, from_agent, scopes):
        return SELF_SENDER_RULE
    if poster:
        return None  # M2 only when poster identity absent
    if normalize_bus_address(from_agent) not in _SHARED_SENDER_NAMES:
        return None
    window_s = ENDPOINT_WAKE_SELF_RECENCY_S
    if window_s <= 0:
        return None
    before = str(turn.get("created_at") or "").strip()
    if not before:
        return None
    recent = _recent_deliveries(store, thread, window_s, before)
    if len(recent) == 1 and recent[0] == target:
        return SELF_SENDER_RECENCY_RULE
    return None


def suppressed_item(item: dict[str, Any], rule_id: str) -> dict[str, Any]:
    """Ledger tombstone for a turn the scan decided not to wake."""
    return {
        **item,
        "decided_by_rule": rule_id,
        "state": "skipped",
        "reason": f"suppressed:{rule_id}",
    }
