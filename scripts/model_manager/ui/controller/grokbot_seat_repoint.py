"""Re-point grok-bot-gc1 and impresario grok wildcard subscriptions from roster probes.

Called from ``GrokbotEgressWatchLoop._context_hop`` after each roster probe.
Writes thread-scoped ``agent_endpoint_subscriptions`` for agent ``grok-bot-gc1``
and thread ``15851`` only when exactly one roster row is named Stage Manager (gc1).
Impresario rows subscribe grok wildcard wake targets and retire matching journals.
"""

from __future__ import annotations

import re
from typing import Any

from agent_seat.registry import STAGE_MANAGER_ADDRESSES
from universal_logging import get_logger

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GC1_ROSTER_NAME,
)

logger = get_logger(__name__)

_GC1_AGENT = "grok-bot-gc1"
_GC1_THREAD_SCOPE = "15851"
_MAILBOX_RE = re.compile(
    r"\bmailbox\s+(?:to=)?([a-z0-9][a-z0-9_-]*)",
    re.IGNORECASE,
)
_EXCLUDED_MAILBOX_SLUGS = STAGE_MANAGER_ADDRESSES | {
    "cursor",
    "maestro",
    "web-anthropic",
    "all",
    "team",
}
_RETIRED_SUFFIX_RE = re.compile(r"\s*\(retired\)\s*$", re.IGNORECASE)


def choose_gc1_agent_id(rows: list[dict[str, Any]]) -> str | None:
    """Return the stripped agent_id when exactly one row matches the gc1 roster name."""
    matches: list[str] = []
    for row in rows:
        if row.get("name") != GC1_ROSTER_NAME:
            continue
        raw = row.get("agent_id")
        if not isinstance(raw, str):
            continue
        agent_id = raw.strip()
        if not agent_id:
            continue
        matches.append(agent_id)
    if len(matches) != 1:
        return None
    return matches[0]


def run_gc1_repoint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Subscribe grok-bot-gc1 to the chosen gc1 agent_id when it differs from the journal."""
    chosen = choose_gc1_agent_id(rows)
    if chosen is None:
        return {"wrote": False, "reason": "not_unique"}

    from agent_bus_store.db.endpoint_subscriptions import (
        current_subscription,
        subscribe_endpoint,
    )
    from cursor_bridge.grok_window_bindings import binding_for

    current = current_subscription(_GC1_AGENT, _GC1_THREAD_SCOPE)
    current_id_raw = (current or {}).get("agent_id")
    current_id = current_id_raw.strip() if isinstance(current_id_raw, str) else None
    if current_id == chosen:
        return {"wrote": False, "reason": "unchanged"}

    display: str | None = None
    if current is not None:
        row_display = current.get("display")
        if isinstance(row_display, str) and row_display.strip():
            display = row_display.strip()
    if display is None:
        binding = binding_for(_GC1_AGENT)
        if binding is not None:
            bound_display = binding.get("display")
            if isinstance(bound_display, str) and bound_display.strip():
                display = bound_display.strip()
    if display is None:
        return {"wrote": False, "reason": "display_unknown"}

    try:
        subscribe_endpoint(
            agent=_GC1_AGENT,
            thread_scope=_GC1_THREAD_SCOPE,
            surface="grok",
            display=display,
            title=GC1_ROSTER_NAME,
            agent_id=chosen,
            bound_by="grok-seat-repoint",
            evidence=f"roster {GC1_ROSTER_NAME}",
        )
    except Exception as exc:
        return {
            "wrote": False,
            "reason": "subscribe_failed",
            "error": type(exc).__name__,
        }
    return {"wrote": True}


def impresario_slugs_for_roster_row(row: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Derive mailbox slugs and thread scopes for one impresario roster row."""
    raw_name = str(row.get("name") or "").strip()
    name = _RETIRED_SUFFIX_RE.sub("", raw_name).strip()
    base_name = re.sub(r"\s*\(\d+\)\s*$", "", name).strip()
    base_slug = re.sub(r"[^a-z0-9]+", "-", base_name.lower()).strip("-")
    full_slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slugs: list[str] = []
    for slug in (base_slug, full_slug):
        if slug and slug not in slugs:
            slugs.append(slug)
    desc = row.get("description")
    if isinstance(desc, str):
        for slug in _MAILBOX_RE.findall(desc.lower()):
            if slug not in _EXCLUDED_MAILBOX_SLUGS and slug not in slugs:
                slugs.append(slug)
    scopes: list[str] = ["*"]
    thread_match = re.search(r"\((\d+)\)\s*$", name)
    if thread_match:
        scopes.append(thread_match.group(1))
    return slugs, scopes


def _live_impresario_display() -> str | None:
    from cursor_bridge.grok_window_bindings import binding_for

    for candidate in ("impresario", _GC1_AGENT):
        binding = binding_for(candidate)
        if binding is not None:
            bound_display = binding.get("display")
            if isinstance(bound_display, str) and bound_display.strip():
                return bound_display.strip()
    return None


def run_impresario_repoint(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Subscribe active Grok impresario roster rows for grok wildcard wake delivery."""
    from agent_bus_store.db.endpoint_subscriptions import (
        SubscribeError,
        current_subscription,
        subscribe_endpoint,
        unsubscribe_endpoint,
    )

    live_display = _live_impresario_display()
    subscribed: list[dict[str, Any]] = []
    unsubscribed: list[dict[str, Any]] = []

    for row in rows:
        name = str(row.get("name") or "").strip()
        agent_id = str(row.get("agent_id") or "").strip()
        if not name or not agent_id:
            continue
        if name == GC1_ROSTER_NAME:
            continue
        if "impresario" not in name.lower():
            continue
        if "(retired)" in name.lower():
            slugs, scopes = impresario_slugs_for_roster_row(row)
            for slug in slugs:
                for scope in scopes:
                    current = current_subscription(slug, scope) or {}
                    if (
                        current.get("state") == "subscribed"
                        and current.get("agent_id") == agent_id
                    ):
                        unsubscribe_endpoint(
                            agent=slug,
                            thread_scope=scope,
                            bound_by="grok-seat-repoint",
                            evidence=f"retired {name}",
                            expected_agent_id=agent_id,
                        )
                        unsubscribed.append(
                            {"agent": slug, "thread_scope": scope, "agent_id": agent_id}
                        )
            continue

    name_counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name or "(retired)" in name.lower():
            continue
        name_counts[name] = name_counts.get(name, 0) + 1

    plans: list[tuple[str, str, list[str], list[str]]] = []
    claims: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        agent_id = str(row.get("agent_id") or "").strip()
        if not name or not agent_id:
            continue
        if "(retired)" in name.lower():
            continue
        if name == GC1_ROSTER_NAME:
            continue
        if "impresario" not in name.lower():
            continue
        if name_counts.get(name, 0) != 1:
            continue
        slugs, scopes = impresario_slugs_for_roster_row(row)
        plans.append((name, agent_id, slugs, scopes))
        for slug in slugs:
            for scope in scopes:
                key = (slug, scope)
                claims.setdefault(key, set()).add(agent_id)

    for name, agent_id, slugs, scopes in plans:
        for slug in slugs:
            for scope in scopes:
                display = live_display
                if display is None:
                    wild = current_subscription(slug, "*") or {}
                    wild_display = wild.get("display")
                    if isinstance(wild_display, str) and wild_display.strip():
                        display = wild_display.strip()
                if len(claims[(slug, scope)]) != 1:
                    logger.warning(
                        "impresario repoint contested agent=%s scope=%s ids=%s",
                        slug,
                        scope,
                        sorted(claims[(slug, scope)]),
                    )
                    continue
                if display is None:
                    continue
                current = current_subscription(slug, scope) or {}
                if (
                    current.get("state") == "subscribed"
                    and current.get("agent_id") == agent_id
                    and current.get("display") == display
                ):
                    continue
                try:
                    subscribe_endpoint(
                        agent=slug,
                        thread_scope=scope,
                        surface="grok",
                        display=display,
                        title=name,
                        agent_id=agent_id,
                        bound_by="grok-seat-repoint",
                        evidence=f"roster {name}",
                    )
                    subscribed.append(
                        {"agent": slug, "thread_scope": scope, "agent_id": agent_id}
                    )
                except SubscribeError as exc:
                    logger.info(
                        "impresario repoint refused agent=%s scope=%s code=%s",
                        slug,
                        scope,
                        exc.code,
                    )

    wrote = len(subscribed) > 0 or len(unsubscribed) > 0
    return {"wrote": wrote, "subscribed": subscribed, "unsubscribed": unsubscribed}


__all__ = [
    "choose_gc1_agent_id",
    "impresario_slugs_for_roster_row",
    "run_gc1_repoint",
    "run_impresario_repoint",
]
