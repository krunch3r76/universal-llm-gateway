"""MCP tool: lighter_api — live Lighter exchange data via public SDK calls.

Read-only access to Lighter DEX: positions, orders, orderbook, balance,
funding rates, markets. When CLAUDEBURST_PERPS_HOST is configured, account-
scoped ops default to the bot's Lighter account (resolved correctly via the
bot REST endpoint — LIGHTER_API_KEY_INDEX is a signing key index, not an
account index, and is never used directly as one). Explicit account_index/
account_address overrides to any account.

Runtime config:
  LIGHTER_BASE_URL        — API endpoint (default: mainnet)
  CLAUDEBURST_PERPS_HOST  — bot host for account index resolution
  CLAUDEBURST_PERPS_PORT  — bot port (default: 8891)
  LIGHTER_API_KEY_INDEX   — used only to detect bot context; NOT passed to Lighter
  LIGHTER_L1_ADDRESS      — fallback L1 address for manual resolution
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_BASE_URL = os.getenv("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")
_DEFAULT_L1_ADDRESS = os.getenv("LIGHTER_L1_ADDRESS", "")
_BOT_ACCOUNT_INDEX_RAW = os.getenv("LIGHTER_API_KEY_INDEX", "").strip()
# LIGHTER_API_KEY_INDEX is a signing key selector — NOT a Lighter account index.
# Never pass this value directly to Lighter account APIs.
# Transparent bot-account routing is done via the bot REST endpoint (CLAUDEBURST_PERPS_HOST).
_BOT_PERPS_HOST = os.getenv("CLAUDEBURST_PERPS_HOST", "")
_BOT_PERPS_PORT = int(os.getenv("CLAUDEBURST_PERPS_PORT", "8891"))
_HAS_BOT_ACCOUNT = bool(_BOT_ACCOUNT_INDEX_RAW or _BOT_PERPS_HOST)
_FUNDING_SORT_FIELDS = {"market_id", "symbol", "exchange", "rate", "abs_rate"}
_FUNDING_GROUP_FIELDS = {"market_id", "symbol", "exchange"}


async def _get_api() -> Any:
    """Return a fresh ApiClient instance."""
    import lighter

    config = lighter.Configuration(host=_BASE_URL)
    return lighter.ApiClient(configuration=config)


async def _resolve_bot_account_index() -> int | None:
    """Ask the bot for its cached Lighter account index.

    Uses the /account-index endpoint which returns the index resolved at bot
    startup — no live Lighter API call, immune to 429 rate limits.
    """
    if not _BOT_PERPS_HOST:
        return None
    import httpx

    base = f"http://{_BOT_PERPS_HOST}:{_BOT_PERPS_PORT}"
    try:
        async with httpx.AsyncClient(base_url=base, timeout=5.0) as client:
            resp = await client.get("/account-index")
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                logger.warning("Bot account index not ready: %s", data["error"])
                return None
            return int(data["account_index"])
    except Exception as exc:
        logger.warning("Could not resolve bot account index from %s: %s", base, exc)
        return None


async def _resolve_account_index(
    api: Any,
    *,
    account_address: str,
    account_index: int | None,
) -> int:
    """Resolve account index from an explicit account selector."""
    import lighter

    if account_index is not None:
        return account_index
    if not account_address:
        raise ValueError(
            "account_address or account_index is required for positions, orders, and balance"
        )

    acct_api = lighter.AccountApi(api)
    resp = await acct_api.accounts_by_l1_address(l1_address=account_address)
    if not resp.sub_accounts:
        raise RuntimeError(f"No account for L1 address {account_address}")
    return int(resp.sub_accounts[0].index)


async def _op_positions(api: Any, idx: int) -> dict[str, Any]:
    import lighter

    acct_api = lighter.AccountApi(api)
    resp = await acct_api.account(by="index", value=str(idx))
    acct = resp.accounts[0]

    positions: list[dict[str, Any]] = []
    total_value = 0.0
    for pos in getattr(acct, "positions", []) or []:
        value = abs(float(pos.position_value))
        if value <= 0:
            continue
        side = "long" if pos.sign > 0 else "short"
        entry = float(pos.avg_entry_price)
        positions.append(
            {
                "market_id": int(pos.market_id),
                "side": side,
                "size_usdc": round(value, 2),
                "entry_price": entry,
                "tied_orders": int(pos.position_tied_order_count),
            }
        )
        total_value += value

    return {
        "positions": positions,
        "count": len(positions),
        "total_exposure": round(total_value, 2),
        "collateral": float(acct.collateral),
    }


async def _op_orders(api: Any, idx: int) -> dict[str, Any]:
    import lighter

    orders: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()
    order_api = lighter.OrderApi(api)
    markets = await order_api.order_books()
    for market in getattr(markets, "order_books", []) or []:
        market_id = int(getattr(market, "market_id", 0) or 0)
        if market_id <= 0:
            continue
        response = await order_api.account_active_orders(
            account_index=idx,
            market_id=market_id,
        )
        for order in getattr(response, "orders", []) or []:
            order_id = str(getattr(order, "order_id", ""))
            if order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
            orders.append(
                {
                    "order_id": order_id,
                    "market_id": int(getattr(order, "market_index", market_id) or market_id),
                    "side": str(getattr(order, "side", "")),
                    "price": str(getattr(order, "price", "")),
                    "size": str(getattr(order, "remaining_base_amount", "")),
                    "type": str(getattr(order, "type", "")),
                    "status": str(getattr(order, "status", "")),
                    "trigger_price": str(getattr(order, "trigger_price", "")),
                }
            )
    return {"orders": orders, "count": len(orders)}


async def _op_balance(
    api: Any, idx: int, account_address: str | None
) -> dict[str, Any]:
    import lighter

    acct_api = lighter.AccountApi(api)
    resp = await acct_api.account(by="index", value=str(idx))
    acct = resp.accounts[0]

    return {
        "collateral": float(acct.collateral),
        "account_index": idx,
        "account_address": account_address,
    }


async def _op_orderbook(api: Any, market_id: int, depth: int) -> dict[str, Any]:
    import lighter

    order_api = lighter.OrderApi(api)
    ob = await order_api.order_book_orders(market_id, depth)

    bids = [
        {"price": float(o.price), "size": float(o.remaining_base_amount)}
        for o in (ob.bids or [])
    ]
    asks = [
        {"price": float(o.price), "size": float(o.remaining_base_amount)}
        for o in (ob.asks or [])
    ]

    mid = None
    if bids and asks:
        mid = round((bids[0]["price"] + asks[0]["price"]) / 2, 4)

    return {
        "market_id": market_id,
        "bids": bids,
        "asks": asks,
        "mid_price": mid,
        "depth": depth,
    }


def _sort_funding_rates(
    rates: list[dict[str, Any]], sort_by: str
) -> list[dict[str, Any]]:
    """Return a deterministically sorted funding snapshot."""
    if not sort_by:
        return rates

    descending = sort_by.startswith("-")
    field = sort_by[1:] if descending else sort_by
    if field not in _FUNDING_SORT_FIELDS:
        raise ValueError(
            "sort_by must be one of: market_id, symbol, exchange, rate, abs_rate"
        )

    def _rate_key(row: dict[str, Any]) -> float:
        return abs(float(row["rate"]))

    def _field_key(row: dict[str, Any]) -> Any:
        return row[field]

    key_fn = _rate_key if field == "abs_rate" else _field_key
    return sorted(rates, key=key_fn, reverse=descending)


def _group_funding_rates(
    rates: list[dict[str, Any]], group_by: str
) -> list[dict[str, Any]]:
    """Group funding rows after any requested sorting has been applied."""
    if not group_by:
        return []
    if group_by not in _FUNDING_GROUP_FIELDS:
        raise ValueError("group_by must be one of: market_id, symbol, exchange")

    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rates:
        grouped[row[group_by]].append(row)

    return [
        {"value": value, "count": len(grouped_rows), "funding_rates": grouped_rows}
        for value, grouped_rows in grouped.items()
    ]


async def _op_funding(
    api: Any, *, sort_by: str = "", group_by: str = ""
) -> dict[str, Any]:
    import lighter

    funding_api = lighter.FundingApi(api)
    resp = await funding_api.funding_rates()
    rates: list[dict[str, Any]] = []
    if hasattr(resp, "funding_rates"):
        for fr in resp.funding_rates:
            rates.append(
                {
                    "market_id": int(fr.market_id),
                    "symbol": str(getattr(fr, "symbol", "")),
                    "exchange": str(getattr(fr, "exchange", "")),
                    "rate": float(fr.rate),
                }
            )

    sorted_rates = _sort_funding_rates(rates, sort_by.strip())
    result: dict[str, Any] = {"funding_rates": sorted_rates, "count": len(sorted_rates)}
    normalized_group_by = group_by.strip()
    if sort_by:
        result["sort_by"] = sort_by.strip()
    if normalized_group_by:
        result["group_by"] = normalized_group_by
        result["groups"] = _group_funding_rates(sorted_rates, normalized_group_by)
    return result


async def _op_markets(api: Any) -> dict[str, Any]:
    import lighter

    order_api = lighter.OrderApi(api)
    obs = await order_api.order_books()
    markets: list[dict[str, Any]] = []
    for ob in obs.order_books:
        markets.append(
            {
                "market_id": int(ob.market_id),
                "symbol": str(ob.symbol),
                "size_decimals": int(getattr(ob, "supported_size_decimals", 4)),
                "price_decimals": int(getattr(ob, "supported_price_decimals", 2)),
            }
        )
    return {"markets": markets, "count": len(markets)}


def register_lighter_api_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def lighter_api(
        op: str = "positions",
        market_id: int = 0,
        depth: int = 5,
        account_address: str = "",
        account_index: int | None = None,
        sort_by: str = "",
        group_by: str = "",
    ) -> dict[str, Any]:
        """Query the public Lighter DEX API — live exchange state, not local DB.

        Use lighter_trades for the bot's local trade log (DB).
        Use claudeburst_perps for bot control (status, kill, pause) and bot-local
        state. For live exchange data (positions, orders, balance), this tool and
        claudeburst_perps(op="live_account") return the same source of truth.
        This tool is intentionally read-only and does not use wallet credentials.

        Operations:
          positions — open positions with entry price, exposure, tied orders
          orders    — open orders on the exchange
          balance   — collateral/equity
          orderbook — bids/asks for a market (requires market_id, depth default 5)
          funding   — current funding rates for all markets; preserves symbol and exchange
          markets   — list all available markets with metadata

        Account-scoped operations:
          positions / orders / balance require an account identifier.
          If neither `account_index` nor `account_address` is provided:
            - When the perps bot is configured (CLAUDEBURST_PERPS_HOST), the bot's
              Lighter account index is resolved automatically via the bot REST API
              (response includes `_source: "bot_account"` and `_account_index`).
            - Otherwise, falls back to `LIGHTER_L1_ADDRESS` if set.
          Pass an explicit `account_index` or `account_address` to inspect a
          different account.
          Note: LIGHTER_API_KEY_INDEX is a signing key selector, not an account
          index — account 5 ≠ key index 5. Never use the key index as an account.
          Public operations (markets / funding / orderbook) do not require either.

        Funding-specific output controls:
          Use sort_by for deterministic inspection output. Valid fields:
          market_id, symbol, exchange, rate, abs_rate. Prefix with '-' for descending.
          Use group_by to bucket funding rows by market_id, symbol, or exchange.
        """
        t0 = monotonic_now()
        api = await _get_api()
        try:
            resolved_address = account_address.strip() or _DEFAULT_L1_ADDRESS or None
            resolved_index = account_index
            used_bot_default = False
            idx: int | None = None
            if op in ("positions", "orders", "balance"):
                if (
                    resolved_index is None
                    and not account_address.strip()
                    and _HAS_BOT_ACCOUNT
                ):
                    # Resolve actual Lighter account index from the bot — LIGHTER_API_KEY_INDEX
                    # is a signing key selector (not an account index; key 5 ≠ account 5).
                    resolved_index = await _resolve_bot_account_index()
                    if resolved_index is not None:
                        used_bot_default = True
                idx = await _resolve_account_index(
                    api,
                    account_address=resolved_address or "",
                    account_index=resolved_index,
                )

            if op == "positions":
                if idx is None:
                    return {"error": "account_address or account_index is required"}
                result = await _op_positions(api, idx)
            elif op == "orders":
                if idx is None:
                    return {"error": "account_address or account_index is required"}
                result = await _op_orders(api, idx)
            elif op == "balance":
                if idx is None:
                    return {"error": "account_address or account_index is required"}
                result = await _op_balance(api, idx, resolved_address)
            elif op == "orderbook":
                if market_id <= 0:
                    return {"error": "market_id required for orderbook op"}
                result = await _op_orderbook(api, market_id, depth)
            elif op == "funding":
                result = await _op_funding(
                    api,
                    sort_by=sort_by,
                    group_by=group_by,
                )
            elif op == "markets":
                result = await _op_markets(api)
            else:
                return {
                    "error": f"Unknown op: {op}. Use: positions, orders, balance, orderbook, funding, markets"
                }
        except Exception as exc:
            record("mcp.lighter.api.error", op=op, error=str(exc))
            return {"error": f"Lighter API call failed: {exc}"}
        finally:
            await api.close()

        duration = monotonic_now() - t0
        record("mcp.lighter.api.queried", op=op, duration_s=round(duration, 3))
        if used_bot_default and isinstance(result, dict):
            result["_source"] = "bot_account"
            result["_account_index"] = idx
        return result
