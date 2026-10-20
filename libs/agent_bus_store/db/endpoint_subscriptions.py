"""Append-only endpoint subscription journal."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from agent_seat.registry import normalize_bus_address
from cursor_bridge.wake_delivery import normalize_target

from .connection import connect, write_connect

_GROK_BOT = re.compile(r"^grok-bot-")
_IDENTITY = ("state", "surface", "display", "title", "agent_id")


class SubscribeError(Exception):
    """Refused subscribe. ``code`` is the HTTP detail code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def current_subscription(agent: str, thread_scope: str) -> dict[str, Any] | None:
    """Return the newest row for one (agent, thread_scope), if any."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? ORDER BY id DESC LIMIT 1",
            (agent, thread_scope),
        ).fetchone()
    return _row(row)


def list_subscriptions(
    agent: str | None = None, thread_scope: str | None = None
) -> list[dict[str, Any]]:
    """List current rows (MAX id per key), optionally filtered."""
    clauses = [
        "id IN (SELECT MAX(id) FROM agent_endpoint_subscriptions GROUP BY agent, thread_scope)"
    ]
    params: list[str] = []
    if agent is not None:
        clauses.append("agent=?")
        params.append(agent)
    if thread_scope is not None:
        clauses.append("thread_scope=?")
        params.append(thread_scope)
    sql = (
        "SELECT * FROM agent_endpoint_subscriptions WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id"
    )
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row(r) for r in rows if r is not None]


def resolve_endpoint(agent: str, thread_id: str) -> tuple[str, dict[str, Any]] | None:
    """Exact scope, parent-thread scope, then grok wildcard."""
    from .endpoint_wake_ledger import resolve_subscription_for_thread

    return resolve_subscription_for_thread(agent, thread_id)


def _same(current: dict[str, Any], nxt: dict[str, Any]) -> bool:
    return all(current.get(k) == nxt.get(k) for k in _IDENTITY)


def subscribe_endpoint(
    *,
    agent: str,
    thread_scope: str,
    surface: str,
    display: str,
    title: str | None = None,
    agent_id: str | None = None,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Append a subscribed row, or return the current row when identical."""
    canon = normalize_bus_address(agent)
    if canon == "web-anthropic":
        raise SubscribeError("endpoint_cse_delegated")
    scope = thread_scope.strip()
    surface_raw = str(surface).strip().lower()
    wildcard_ok = (
        surface_raw == "grok"
        and canon not in {"all", "team"}
        and (
            _GROK_BOT.match(canon) is not None
            or canon == "stage-manager"
            or canon == "insyncerator"
        )
    ) or (surface_raw == "cse" and canon == "maestro")
    if scope == "*" and not wildcard_ok:
        raise SubscribeError("endpoint_wildcard_refused")
    if not (title or agent_id):
        raise SubscribeError("endpoint_title_or_agent_required")
    if surface_raw == "cse":
        if not agent_id:
            raise SubscribeError("endpoint_title_or_agent_required")
        surface_n = "cse"
        display_n = str(display or "cse").strip().lower() or "cse"
    else:
        target = normalize_target(display, surface)
        if not target.get("ok"):
            raise SubscribeError("endpoint_target_refused")
        surface_n = str(target["app"])
        display_n = str(target["display"])
    proposed = {
        "state": "subscribed",
        "surface": surface_n,
        "display": display_n,
        "title": title,
        "agent_id": agent_id,
    }
    with write_connect() as conn:
        if scope != "*":
            exists = conn.execute(
                "SELECT id FROM threads WHERE id=?", (scope,)
            ).fetchone()
            if exists is None:
                raise SubscribeError("endpoint_thread_missing")
        current = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? ORDER BY id DESC LIMIT 1",
            (canon, scope),
        ).fetchone()
        current_d = _row(current)
        if current_d is not None and _same(current_d, proposed):
            return {
                "appended": False,
                "row": current_d,
                "prior": None,
                "prior_subscription_id": (
                    int(current_d["id"]) if current_d.get("id") is not None else None
                ),
            }
        prior_for_response = current_d
        prior_subscription_id: int | None = None
        if current_d is not None and current_d.get("id") is not None:
            prior_subscription_id = int(current_d["id"])
        if (
            current_d is not None
            and current_d.get("state") == "subscribed"
            and (
                current_d.get("agent_id") != agent_id
                or current_d.get("surface") != surface_n
            )
        ):
            tomb_evidence = f"superseded_by:{agent_id}"
            conn.execute(
                "INSERT INTO agent_endpoint_subscriptions "
                "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    canon,
                    scope,
                    "unsubscribed",
                    current_d["surface"],
                    current_d["display"],
                    current_d["title"],
                    current_d["agent_id"],
                    bound_by,
                    tomb_evidence,
                ),
            )
            prior_for_response = _row(
                conn.execute(
                    "SELECT * FROM agent_endpoint_subscriptions "
                    "WHERE agent=? AND thread_scope=? ORDER BY id DESC LIMIT 1",
                    (canon, scope),
                ).fetchone()
            )
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                canon,
                scope,
                "subscribed",
                surface_n,
                display_n,
                title,
                agent_id,
                bound_by,
                evidence,
            ),
        )
        new_id = int(cur.lastrowid)
        row = _row(
            conn.execute(
                "SELECT * FROM agent_endpoint_subscriptions WHERE id=?", (new_id,)
            ).fetchone()
        )
    assert row is not None
    return {
        "appended": True,
        "row": row,
        "prior": prior_for_response,
        "prior_subscription_id": prior_subscription_id,
    }


def unsubscribe_endpoint(
    *,
    agent: str,
    thread_scope: str,
    bound_by: str | None = None,
    evidence: str | None = None,
) -> dict[str, Any]:
    """Append an unsubscribed tombstone, or no-op when already tombstoned."""
    canon = normalize_bus_address(agent)
    scope = thread_scope.strip()
    with write_connect() as conn:
        current = conn.execute(
            "SELECT * FROM agent_endpoint_subscriptions "
            "WHERE agent=? AND thread_scope=? ORDER BY id DESC LIMIT 1",
            (canon, scope),
        ).fetchone()
        current_d = _row(current)
        if current_d is None or current_d["state"] == "unsubscribed":
            return {"appended": False, "row": current_d, "prior": current_d}
        cur = conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, bound_by, evidence) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                canon,
                scope,
                "unsubscribed",
                current_d["surface"],
                current_d["display"],
                current_d["title"],
                current_d["agent_id"],
                bound_by,
                evidence,
            ),
        )
        new_id = int(cur.lastrowid)
        row = _row(
            conn.execute(
                "SELECT * FROM agent_endpoint_subscriptions WHERE id=?", (new_id,)
            ).fetchone()
        )
    assert row is not None
    return {"appended": True, "row": row, "prior": current_d}
