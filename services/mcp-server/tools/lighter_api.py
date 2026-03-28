"""MCP tool: lighter_api — live Lighter exchange data via public SDK calls.

Read-only access to Lighter DEX: positions, orders, orderbook, balance,
funding rates, markets. Complements lighter_trades (which reads the bot's
local DB) by querying the exchange directly.

Runtime config:
  LIGHTER_BASE_URL — API endpoint (default: mainnet)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_BASE_URL = os.getenv("LIGHTER_BASE_URL", "https://mainnet.zklighter.elliot.ai")


async def _get_api() -> Any:
    """Return a fresh ApiClient instance."""
    import lighter

    config = lighter.Configuration(host=_BASE_URL)
    return lighter.ApiClient(configuration=config)


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

    acct_api = lighter.AccountApi(api)
    resp = await acct_api.account(by="index", value=str(idx))
    acct = resp.accounts[0]

    orders: list[dict[str, Any]] = []
    for order in getattr(acct, "open_orders", []) or []:
        orders.append(
            {
                "order_id": str(getattr(order, "order_id", "")),
                "market_id": int(getattr(order, "market_id", 0)),
                "side": str(getattr(order, "side", "")),
                "price": str(getattr(order, "price", "")),
                "size": str(getattr(order, "remaining_base_amount", "")),
                "type": str(getattr(order, "type", "")),
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


async def _op_funding(api: Any) -> dict[str, Any]:
    import lighter

    funding_api = lighter.FundingApi(api)
    resp = await funding_api.funding_rates()
    rates: list[dict[str, Any]] = []
    if hasattr(resp, "funding_rates"):
        for fr in resp.funding_rates:
            rates.append(
                {
                    "market_id": int(fr.market_id),
                    "rate": float(fr.rate),
                }
            )
    return {"funding_rates": rates, "count": len(rates)}


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
        account_index: int = 0,
    ) -> dict[str, Any]:
        """Query the public Lighter DEX API — live exchange state, not local DB.

        Use lighter_trades for the bot's local trade log (DB).
        Use lighter_api for live exchange truth (positions, orders, book, balance).
        This tool is intentionally read-only and does not use wallet credentials.

        Operations:
          positions — open positions with entry price, exposure, tied orders
          orders    — open orders on the exchange
          balance   — collateral/equity
          orderbook — bids/asks for a market (requires market_id, depth default 5)
          funding   — current funding rates for all markets
          markets   — list all available markets with metadata

        Account-scoped operations:
          positions / orders / balance require either account_address or account_index.
          Public operations (markets / funding / orderbook) do not require either.
        """
        t0 = monotonic_now()
        api = await _get_api()
        try:
            resolved_address = account_address.strip() or None
            resolved_index = account_index if account_index > 0 else None
            if op in ("positions", "orders", "balance"):
                idx = await _resolve_account_index(
                    api,
                    account_address=resolved_address or "",
                    account_index=resolved_index,
                )

            if op == "positions":
                result = await _op_positions(api, idx)
            elif op == "orders":
                result = await _op_orders(api, idx)
            elif op == "balance":
                result = await _op_balance(api, idx, resolved_address)
            elif op == "orderbook":
                if market_id <= 0:
                    return {"error": "market_id required for orderbook op"}
                result = await _op_orderbook(api, market_id, depth)
            elif op == "funding":
                result = await _op_funding(api)
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
        return result
