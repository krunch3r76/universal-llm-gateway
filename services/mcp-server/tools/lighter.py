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
    "regime": [],
    "sizing": [],
    "positions": [],
    "orders": [],
    "balance": [],
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


async def _route_bot_local(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route bot-local ops to lighter_trades (SQLite) or bot REST API."""
    if op == "status":
        from .claudeburst_perps_common import call_bot, enrich_perps_result

        result = await call_bot("GET", "/status")
        if "error" not in result:
            result = await enrich_perps_result("status", result)
        return result

    if op == "regime":
        from .claudeburst_perps_common import call_bot

        return await call_bot("GET", "/regime")

    if op == "sizing":
        from .claudeburst_perps_common import call_bot

        return await call_bot("GET", "/sizing")

    if op == "logs":
        from .lighter_trades import _op_logs

        return _op_logs(args.get("lines", 50))

    from .lighter_trades import (
        _connect,
        _op_pnl,
        _op_signals,
        _op_trades,
    )

    conn = _connect()
    if conn is None:
        return {"error": "Trade log DB not available"}
    try:
        if op == "trades":
            return _op_trades(conn, args.get("limit", 20), args.get("status", "all"))
        if op == "signals":
            return _op_signals(conn, args.get("limit", 20))
        if op == "pnl":
            return _op_pnl(conn)
    finally:
        conn.close()
    return {"error": f"Unhandled bot-local op: {op}"}


async def _route_exchange(op: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route exchange ops to lighter SDK or bot REST API (fills)."""
    if op == "fills":
        from .claudeburst_perps_common import call_bot

        params: dict[str, Any] = {"limit": max(1, min(args.get("limit", 50), 200))}
        if args.get("market"):
            params["market"] = args["market"]
        if args.get("since"):
            params["since"] = args["since"]
        return await call_bot("GET", "/account/history", params=params)

    from .lighter_api import (
        _HAS_BOT_ACCOUNT,
        _get_api,
        _op_balance,
        _op_funding,
        _op_markets,
        _op_orderbook,
        _op_orders,
        _op_positions,
        _resolve_account_index,
        _resolve_bot_account_index,
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

        account_address = args.get("account_address", "")
        account_index = args.get("account_index")
        if account_index is not None:
            account_index = int(account_index)

        if account_index is None and not account_address and _HAS_BOT_ACCOUNT:
            account_index = await _resolve_bot_account_index()

        idx = await _resolve_account_index(
            api, account_address=account_address, account_index=account_index
        )

        if op == "positions":
            return await _op_positions(api, idx)
        if op == "orders":
            return await _op_orders(api, idx)
        if op == "balance":
            return await _op_balance(api, idx, account_address or None)
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
    {"status", "trades", "signals", "pnl", "logs", "regime", "sizing"}
)
_EXCHANGE_OPS = frozenset(
    {"positions", "orders", "balance", "orderbook", "funding", "markets", "fills"}
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
          signals   — recent signals (limit?)
          pnl       — aggregated PnL (limit?)
          logs      — bot log tail (lines?)
          regime    — current regime state, vol metrics, transition history
          sizing    — per-strategy Kelly stats and current effective sizes

        Ops (exchange — live Lighter API):
          positions — open positions, authoritative (account_index?, account_address?)
          orders    — open orders (account_index?, account_address?)
          balance   — collateral/equity (account_index?, account_address?)
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
