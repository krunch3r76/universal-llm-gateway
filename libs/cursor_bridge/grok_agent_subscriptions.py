"""Grok endpoint subscriptions over the agent-bus HTTP routes.

Uses ``bus_watch.digest_budget._bus`` (unix-socket httpx), the same client
``cursor_xvfb_subscription`` uses. This module does not open sqlite.
"""

from __future__ import annotations

from typing import Any

import httpx

logger_name = "cursor_bridge.grok_agent_subscriptions"


def _logger() -> Any:
    from universal_logging import get_logger

    return get_logger(logger_name)


def _client() -> Any:
    from bus_watch.digest_budget import _bus

    return _bus()


def _code_from_response(resp: Any) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return "subscription_failed"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict) and detail.get("code"):
        return str(detail["code"])
    return "subscription_failed"


def preflight_subscriptions() -> dict[str, Any]:
    """GET the subscription index. Failure blocks a mailbox spawn before create."""
    try:
        with _client() as bus:
            resp = bus.get("/endpoint-subscriptions")
    except SystemExit as exc:
        return {"ok": False, "code": "subscription_unavailable", "reason": str(exc)[:200]}
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "code": "subscription_unavailable",
            "reason": type(exc).__name__,
        }
    except Exception as exc:
        _logger().exception("grok subscription preflight failed")
        return {
            "ok": False,
            "code": "subscription_unavailable",
            "reason": type(exc).__name__,
        }
    if resp.status_code >= 400:
        return {
            "ok": False,
            "code": _code_from_response(resp) if resp.status_code == 422 else "subscription_unavailable",
            "status_code": resp.status_code,
        }
    return {"ok": True, "status_code": resp.status_code}


def subscribe_grok(
    *,
    mailbox: str,
    thread_scope: str,
    display: str,
    agent_id: str,
    title: str,
) -> dict[str, Any]:
    """POST ``/endpoint-subscriptions`` after ``SubscribeBody`` validation."""
    from agent_bus_store.routes.threads.subscriptions import SubscribeBody

    body = SubscribeBody(
        agent=mailbox,
        thread_scope=thread_scope,
        surface="grok",
        display=display,
        title=title,
        agent_id=agent_id,
        bound_by="grok_agent_ops.spawn",
        evidence="grok_agent_ops.spawn",
    ).model_dump()
    try:
        with _client() as bus:
            resp = bus.post("/endpoint-subscriptions", json=body)
    except SystemExit as exc:
        return {"ok": False, "code": "subscription_unavailable", "reason": str(exc)[:200]}
    except httpx.TimeoutException as exc:
        return {"ok": False, "code": "subscription_timeout", "reason": type(exc).__name__}
    except httpx.HTTPError as exc:
        return {"ok": False, "code": "subscription_failed", "reason": type(exc).__name__}
    except Exception as exc:
        _logger().exception("grok subscribe failed")
        return {"ok": False, "code": "subscription_failed", "reason": type(exc).__name__}
    if resp.status_code == 422:
        return {"ok": False, "code": _code_from_response(resp), "status_code": 422}
    if resp.status_code >= 400:
        return {"ok": False, "code": "subscription_failed", "status_code": resp.status_code}
    return {"ok": True, "status_code": resp.status_code}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("subscriptions", "rows", "items"):
            raw = payload.get(key)
            if isinstance(raw, list):
                return [row for row in raw if isinstance(row, dict)]
    return []


def unsubscribe_grok(*, agent_id: str, display: str) -> dict[str, Any]:
    """Unsubscribe matching grok rows. Partial failures stay in ``errors``."""
    try:
        with _client() as bus:
            listed = bus.get("/endpoint-subscriptions")
            if listed.status_code >= 400:
                return {
                    "ok": False,
                    "code": "subscription_unavailable",
                    "unsubscribed": [],
                    "unsubscribe_errors": [{"code": "list_failed", "status_code": listed.status_code}],
                }
            try:
                payload = listed.json()
            except ValueError:
                payload = []
            touched: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            for row in _rows(payload):
                if row.get("state") not in (None, "subscribed"):
                    continue
                if str(row.get("surface") or "") != "grok":
                    continue
                if str(row.get("display") or "") != display:
                    continue
                if str(row.get("agent_id") or "") != agent_id:
                    continue
                from agent_bus_store.routes.threads.subscriptions import UnsubscribeBody

                body = UnsubscribeBody(
                    agent=str(row.get("agent") or ""),
                    thread_scope=str(row.get("thread_scope") or ""),
                    bound_by="grok_agent_ops.retire",
                    evidence="grok_agent_ops.retire",
                    expected_agent_id=agent_id,
                ).model_dump()
                try:
                    resp = bus.post("/endpoint-subscriptions/unsubscribe", json=body)
                except (httpx.HTTPError, SystemExit) as exc:
                    errors.append({"code": "unsubscribe_failed", "reason": type(exc).__name__, "agent": body["agent"]})
                    continue
                if resp.status_code >= 400:
                    errors.append(
                        {
                            "code": _code_from_response(resp),
                            "status_code": resp.status_code,
                            "agent": body["agent"],
                        }
                    )
                else:
                    touched.append({"agent": body["agent"], "thread_scope": body["thread_scope"]})
    except (httpx.HTTPError, SystemExit, Exception) as exc:
        _logger().exception("grok unsubscribe list failed")
        return {
            "ok": False,
            "code": "unsubscribe_partial",
            "unsubscribed": [],
            "unsubscribe_errors": [{"code": "subscription_unavailable", "reason": type(exc).__name__}],
        }
    ok = not errors
    return {
        "ok": ok,
        "code": "unsubscribed" if ok else "unsubscribe_partial",
        "unsubscribed": touched,
        "unsubscribe_errors": errors,
    }


__all__ = ["preflight_subscriptions", "subscribe_grok", "unsubscribe_grok"]
