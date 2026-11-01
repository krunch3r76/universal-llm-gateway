"""Repoint predecessor tab ids on endpoint subscriptions after continuity resume."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from bus_watch.cursor_xvfb_subscription import subscribe_cursor_xvfb_house
from bus_watch.digest_budget import _bus, _get

logger = logging.getLogger(__name__)

_AGENT = "cursor"
_MAX_PARENT_HOPS = 8


def _lane_parent_default(thread_id: str, *, client: httpx.Client | None = None) -> str | None:
    try:
        if client is None:
            with _bus() as bus:
                payload = _get(bus, f"/threads/{thread_id}/lane-current")
        else:
            payload = _get(client, f"/threads/{thread_id}/lane-current")  # type: ignore[arg-type]
    except (Exception, SystemExit):
        return None
    if not isinstance(payload, dict):
        return None
    parent = payload.get("parent_thread")
    if not isinstance(parent, str) or not parent.strip():
        return None
    return parent.strip()


def _list_cursor_subscriptions(
    *, client: httpx.Client | None = None
) -> list[dict[str, Any]]:
    try:
        if client is None:
            with _bus() as bus:
                payload = _get(bus, "/endpoint-subscriptions", agent=_AGENT)
        else:
            payload = _get(client, "/endpoint-subscriptions", agent=_AGENT)  # type: ignore[arg-type]
    except (Exception, SystemExit) as exc:
        raise RuntimeError(f"list_subscriptions:{type(exc).__name__}") from exc
    rows = payload.get("subscriptions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("state") == "subscribed"]


def current_root_subscription_agent_id(
    root: str, *, client: httpx.Client | None = None
) -> str | None:
    """Return ``agent_id`` on the current ``cursor`` subscription for ``root``."""
    try:
        if client is None:
            with _bus() as bus:
                payload = _get(
                    bus,
                    "/endpoint-subscriptions",
                    agent=_AGENT,
                    thread_scope=root,
                )
        else:
            payload = _get(
                client,
                "/endpoint-subscriptions",
                agent=_AGENT,
                thread_scope=root,
            )  # type: ignore[arg-type]
    except (Exception, SystemExit):
        return None
    if not isinstance(payload, dict):
        return None
    subs = payload.get("subscriptions") or []
    if not subs:
        return None
    row = subs[0] if isinstance(subs[0], dict) else None
    if not row or row.get("state") != "subscribed":
        return None
    aid = str(row.get("agent_id") or "").strip()
    return aid or None


def scope_reaches_root(
    scope: str,
    root: str,
    lane_parent: Callable[[str], str | None],
) -> bool:
    """True when ``scope`` is the root, wildcard, or a child lane under ``root``."""
    root_s = str(root).strip()
    scope_s = str(scope).strip()
    if not root_s or not scope_s:
        return False
    if scope_s in {root_s, "*"}:
        return True
    seen: set[str] = set()
    cur: str | None = scope_s
    for _ in range(_MAX_PARENT_HOPS):
        if not cur or cur in seen:
            break
        if cur == root_s:
            return True
        seen.add(cur)
        cur = lane_parent(cur)
    return False


def rebind_predecessor_subscriptions(
    root: str,
    new_agent_id: str,
    *,
    prior_agent_id: str | None = None,
    bound_by: str = "continuity-resume-rebind",
    client: httpx.Client | None = None,
    list_subscriptions: Callable[[], list[dict[str, Any]]] | None = None,
    lane_parent: Callable[[str], str | None] | None = None,
    subscribe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Re-subscribe stale predecessor rows for one continuity root mailbox."""
    root_s = str(root or "").strip()
    new_id = str(new_agent_id or "").strip()
    if not root_s or not new_id:
        return {"ok": False, "reason": "root_and_new_agent_id_required"}

    list_fn = list_subscriptions or (lambda: _list_cursor_subscriptions(client=client))
    parent_fn = lane_parent or (lambda tid: _lane_parent_default(tid, client=client))
    sub_fn = subscribe or subscribe_cursor_xvfb_house

    try:
        prior = str(prior_agent_id or "").strip() or None
        if prior is None:
            prior = current_root_subscription_agent_id(root_s, client=client)
        if not prior:
            return {
                "ok": True,
                "repointed": [],
                "skipped": [],
                "reason": "no_prior_agent_id",
            }
        if prior == new_id:
            return {"ok": True, "repointed": [], "skipped": [], "reason": "already_current"}

        rows = list_fn()
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        logger.exception("hop_rebind: list failed root=%s", root_s)
        return {
            "ok": False,
            "reason": f"list_failed:{type(exc).__name__}",
            "detail": str(exc)[:200],
        }

    repointed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        if str(row.get("agent") or _AGENT) != _AGENT:
            skipped.append({"thread_scope": row.get("thread_scope"), "reason": "foreign_agent"})
            continue
        scope = str(row.get("thread_scope") or "").strip()
        row_aid = str(row.get("agent_id") or "").strip()
        if row_aid != prior:
            skipped.append({"thread_scope": scope, "reason": "foreign_agent_id"})
            continue
        if not scope_reaches_root(scope, root_s, parent_fn):
            skipped.append({"thread_scope": scope, "reason": "other_mailbox"})
            continue
        title = row.get("title")
        display = str(row.get("display") or "8").strip() or "8"
        try:
            result = sub_fn(
                scope,
                new_id,
                display=display,
                title=str(title) if title is not None else None,
                bound_by=bound_by,
                evidence=f"rebind:{prior}->{new_id}",
                client=client,
            )
        except Exception as exc:  # noqa: BLE001
            skipped.append(
                {
                    "thread_scope": scope,
                    "reason": f"subscribe_failed:{type(exc).__name__}",
                }
            )
            continue
        if result.get("ok"):
            repointed.append({"thread_scope": scope, "prior_agent_id": prior})
        else:
            skipped.append(
                {
                    "thread_scope": scope,
                    "reason": str(result.get("reason") or "subscribe_not_ok"),
                }
            )

    return {"ok": True, "repointed": repointed, "skipped": skipped, "prior_agent_id": prior}


__all__ = [
    "current_root_subscription_agent_id",
    "rebind_predecessor_subscriptions",
    "scope_reaches_root",
]
