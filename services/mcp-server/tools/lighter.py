"""Unified Lighter DEX tool — exchange data, bot trade log, shadow orders.

Consolidates lighter_api, lighter_trades, lighter_history, and shadow tools
into a single dispatch surface with op + arguments pattern.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_OP_REQUIRED: dict[str, list[str]] = {
    "status": [],
    "trades": [],
    "signals": [],
    "pnl": [],
    "logs": [],
    "risk": [],
    "health": [],
    "account_index": [],
    "regime": [],
    "sizing": [],
    "positions": [],
    "orders": [],
    "balance": [],
    "live_account": [],
    "live_active_orders": [],
    "live_recent_orders": [],
    "live_resolve": [],
    "orderbook": ["market_id"],
    "funding": [],
    "markets": [],
    "fills": [],
    "shadow_create": ["market", "side", "trigger_price", "trigger_type", "size_usdc"],
    "shadow_list": [],
    "shadow_update": ["order_id"],
    "shadow_cancel": ["order_id"],
    "shadow_log": ["order_id"],
}


def _validate_args(op: str, args: dict[str, Any]) -> str | None:
    """Return an error message if required args are missing, else None."""
    required = _OP_REQUIRED.get(op)
    if required is None:
        ops = ", ".join(sorted(_OP_REQUIRED))
        return f"Unknown op: {op!r}. Available: {ops}"
    missing = [k for k in required if k not in args]
    if missing:
        return f"op={op!r} requires: {', '.join(missing)}"
    return None


def _parse_arguments(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise TypeError(f"arguments must be a JSON object, got {type(parsed).__name__}")
    return parsed


def _account_selector_params(args: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if args.get("account_index") is not None:
        params["account_index"] = int(args["account_index"])
    account_address = str(
        args.get("account_address", "") or args.get("l1_address", "") or ""
    ).strip()
    if account_address:
        params["l1_address"] = account_address
    return params


def _market_id_param(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("market_id") is None:
        return {}
    return {"market_id": int(args["market_id"])}


async def _route_bot_local(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route bot-local ops to lighter_trades (SQLite) or bot REST API."""
    if op in {
        "status",
        "signals",
        "risk",
        "health",
        "account_index",
        "regime",
        "sizing",
    }:
        from .claudeburst_perps_common import call_bot, enrich_perps_result

        rest_paths = {
            "status": "/status",
            "risk": "/risk",
            "health": "/health",
            "account_index": "/account-index",
            "regime": "/regime",
            "sizing": "/sizing",
            "signals": "/signals",
        }
        params: dict[str, Any] = {}
        if op == "signals":
            params["limit"] = max(1, min(int(args.get("limit", 20)), 200))
            if args.get("market"):
                params["market"] = args["market"]

        result = await call_bot("GET", rest_paths[op], params=params or None)
        if "error" not in result and op in {"status", "signals", "risk", "health"}:
            result = await enrich_perps_result(op, result)
        return result

    if op == "logs":
        from .lighter_trades import _op_logs

        return _op_logs(args.get("lines", 50))

    from .lighter_trades import (
        _connect,
        _op_pnl,
        _op_trades,
    )

    conn = _connect()
    if conn is None:
        return {"error": "Trade log DB not available"}
    try:
        if op == "trades":
            return _op_trades(conn, args.get("limit", 20), args.get("status", "all"))
        if op == "pnl":
            return _op_pnl(conn)
    finally:
        conn.close()
    return {"error": f"Unhandled bot-local op: {op}"}


async def _route_exchange(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route exchange ops to bot REST API where available."""
    if op in {"positions", "balance", "live_account"}:
        from .claudeburst_perps_common import call_bot, enrich_perps_result

        result = await call_bot(
            "GET", "/live/account", params=_account_selector_params(args)
        )
        if "error" in result:
            return result
        result = await enrich_perps_result("live_account", result)
        if op == "balance":
            return {
                "account_index": result.get("account_index"),
                "resolved_by": result.get("resolved_by"),
                "collateral": result.get("collateral"),
                "available_balance": result.get("available_balance"),
                "_data_source": result.get("_data_source"),
            }
        if op == "positions":
            result.setdefault("count", result.get("positions_count", 0))
        return result

    if op in {"orders", "live_active_orders"}:
        from .claudeburst_perps_common import call_bot

        params = {
            **_account_selector_params(args),
            **_market_id_param(args),
        }
        return await call_bot("GET", "/live/orders/active", params=params or None)

    if op == "live_recent_orders":
        from .claudeburst_perps_common import call_bot

        params = {
            **_account_selector_params(args),
            **_market_id_param(args),
        }
        if args.get("limit") is not None:
            params["limit"] = max(1, min(int(args["limit"]), 200))
        if args.get("cursor"):
            params["cursor"] = str(args["cursor"])
        return await call_bot("GET", "/live/orders/recent", params=params or None)

    if op == "live_resolve":
        from .claudeburst_perps_common import call_bot

        l1_address = str(
            args.get("account_address", "") or args.get("l1_address", "") or ""
        ).strip()
        if not l1_address:
            return {"error": "op='live_resolve' requires account_address or l1_address"}
        return await call_bot(
            "GET", "/live/resolve-account", params={"l1_address": l1_address}
        )

    if op == "fills":
        from .claudeburst_perps_common import call_bot

        params: dict[str, Any] = {"limit": max(1, min(args.get("limit", 50), 200))}
        if args.get("market"):
            params["market"] = args["market"]
        if args.get("since"):
            params["since"] = args["since"]
        return await call_bot("GET", "/account/history", params=params)

    from .lighter_api import (
        _get_api,
        _op_funding,
        _op_markets,
        _op_orderbook,
    )

    api = await _get_api()
    try:
        if op == "orderbook":
            return await _op_orderbook(
                api, int(args["market_id"]), args.get("depth", 5)
            )
        if op == "funding":
            return await _op_funding(
                api, sort_by=args.get("sort_by", ""), group_by=args.get("group_by", "")
            )
        if op == "markets":
            return await _op_markets(api)
    except Exception as exc:
        record("mcp.lighter.error", op=op, error=str(exc))
        return {"error": f"Lighter API call failed: {exc}"}
    finally:
        await api.close()
    return {"error": f"Unhandled exchange op: {op}"}


async def _route_shadow(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route shadow order ops to bot REST API."""
    from .claudeburst_perps_common import call_bot

    if op == "shadow_create":
        body = {
            "market": args["market"],
            "side": args["side"],
            "trigger_price": args["trigger_price"],
            "trigger_type": args["trigger_type"],
            "size_usdc": args["size_usdc"],
            "order_type": args.get("order_type", "take_profit"),
            "review_window_seconds": args.get("review_window_seconds", 30),
        }
        if args.get("notes"):
            body["notes"] = args["notes"]
        return await call_bot("POST", "/shadow-orders", body=body)
    if op == "shadow_list":
        params: dict[str, Any] = {}
        if args.get("status"):
            params["status"] = args["status"]
        if args.get("market"):
            params["market"] = args["market"]
        return await call_bot("GET", "/shadow-orders", params=params)
    if op == "shadow_update":
        body = {}
        for key in ("trigger_price", "size_usdc", "review_window_seconds", "notes"):
            if args.get(key) is not None:
                body[key] = args[key]
        if not body:
            return {"error": "Provide at least one field to update"}
        return await call_bot("PATCH", f"/shadow-orders/{args['order_id']}", body=body)
    if op == "shadow_cancel":
        return await call_bot("DELETE", f"/shadow-orders/{args['order_id']}")
    if op == "shadow_log":
        return await call_bot("GET", f"/shadow-orders/{args['order_id']}/log")
    return {"error": f"Unhandled shadow op: {op}"}


_BOT_LOCAL_OPS = frozenset(
    {
        "status",
        "trades",
        "signals",
        "pnl",
        "logs",
        "risk",
        "health",
        "account_index",
        "regime",
        "sizing",
    }
)
_EXCHANGE_OPS = frozenset(
    {
        "positions",
        "orders",
        "balance",
        "live_account",
        "live_active_orders",
        "live_recent_orders",
        "live_resolve",
        "orderbook",
        "funding",
        "markets",
        "fills",
    }
)
_SHADOW_OPS = frozenset(
    {"shadow_create", "shadow_list", "shadow_update", "shadow_cancel", "shadow_log"}
)


def register_lighter_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def lighter(op: str, arguments: str = "{}") -> dict[str, Any]:
        """Unified Lighter DEX tool — exchange data, bot trade log, shadow orders.

        Use this for ALL Lighter read operations and shadow order management.
        For bot control (kill/pause/resume), use bot_control instead.

        Ops (bot-local — from trade DB / bot REST):
          status    — full bot snapshot: positions + risk + signals + reconciliation
          trades    — recent trades (limit?, status?)
          signals   — recent signals from REST/trade log (limit?, market?)
          pnl       — aggregated PnL (limit?)
          logs      — bot log tail (lines?)
          risk      — current bot risk snapshot
          health    — liveness / paused / halted summary
          account_index — cached bot Lighter account index
          regime    — current regime state, vol metrics, transition history
          sizing    — per-strategy Kelly stats and current effective sizes

        Ops (exchange — bot REST over live Lighter account data):
          positions         — `/live/account` payload with authoritative positions
          orders            — active live orders via `/live/orders/active`
          balance           — balance subset from `/live/account`
          live_account      — full `/live/account` payload
          live_active_orders — direct `/live/orders/active` payload
          live_recent_orders — direct `/live/orders/recent` payload
          live_resolve      — resolve account index from `account_address` or `l1_address`
          orderbook — bids/asks (market_id REQUIRED, depth?)
          funding   — funding rates (sort_by?, group_by?)
          markets   — all markets
          fills     — per-tranche fill history (market?, since?, limit?)

        Ops (shadow orders):
          shadow_create — market, side, trigger_price, trigger_type, size_usdc REQUIRED;
                          order_type?, review_window_seconds?, notes?
          shadow_list   — market?, status?
          shadow_update — order_id REQUIRED; trigger_price?, size_usdc?,
                          review_window_seconds?, notes?
          shadow_cancel — order_id REQUIRED
          shadow_log    — order_id REQUIRED
        """
        t0 = monotonic_now()
        try:
            args = _parse_arguments(arguments)
        except (json.JSONDecodeError, TypeError) as exc:
            return {"error": f"Invalid arguments JSON: {exc}"}

        err = _validate_args(op, args)
        if err:
            return {"error": err}

        if op in _BOT_LOCAL_OPS:
            result = await _route_bot_local(op, args)
        elif op in _EXCHANGE_OPS:
            result = await _route_exchange(op, args)
        elif op in _SHADOW_OPS:
            result = await _route_shadow(op, args)
        else:
            result = {"error": f"Unknown op: {op}"}

        duration = monotonic_now() - t0
        record("mcp.lighter.dispatched", op=op, duration_s=round(duration, 3))
        return result
