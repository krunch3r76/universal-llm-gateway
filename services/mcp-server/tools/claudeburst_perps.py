"""MCP tools for the ClaudeBurst Lighter perps bot."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

from .claudeburst_perps_common import OPS, call_bot, enrich_perps_result

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_claudeburst_perps_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def claudeburst_perps(
        op: str,
        account_index: int | None = None,
        l1_address: str | None = None,
        market_id: int | None = None,
        market: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Query or control the Lighter perps trading bot.

        CRITICAL — two data tiers, always check _data_source in every response:

          bot_local (status, positions, risk, health):
            Only positions the BOT itself opened. Manually placed exchange
            positions are INVISIBLE here. Every response includes
            _reconciliation_status: "clean" | "diverged" | "pending".
            If "diverged", _reconciliation_warning names what's missing.
            Never treat bot_local as the full portfolio picture.

          lighter_exchange (live_account, live_active_orders, live_recent_orders):
            Full exchange truth — all positions regardless of origin.
            Use this to see manually placed trades the bot doesn't know about.

        Operations (bot_local):
          status    — Full snapshot: risk, positions, prices, paper/live mode.
          positions — Bot-managed positions with unrealized P&L + reconciliation.
          risk      — Risk state: exposure, halt status, starting equity.
          health    — Liveness check.

        Operations (lighter_exchange):
          live_account       — Live exchange: balance + ALL open positions.
                               This is the authoritative portfolio view.
          live_active_orders — Live exchange active orders.
          live_recent_orders — Recent filled/inactive orders.
          live_resolve       — Resolve `account_index` from `l1_address`.
          signals            — Bot signal feed; optional `market` narrows noise.

        Operations (control — mutating):
          kill   — Activate kill switch. Halts all perps trading.
          pause  — Pause the scan loop.
          resume — Resume the scan loop.

        Recommended pattern for full portfolio view:
          1. claudeburst_perps(op="positions")  → bot-managed + reconciliation state
          2. If _reconciliation_status == "diverged": check _reconciliation_warning
          3. claudeburst_perps(op="live_account") → exchange truth (all positions)
        """
        t0 = monotonic_now()
        entry = OPS.get(op)
        if entry is None:
            ops = ", ".join(sorted(OPS))
            return {"error": f"Unknown op '{op}'. Available: {ops}"}

        method, path = entry
        params: dict[str, str | int] = {}
        if op == "live_resolve":
            if not l1_address:
                return {"error": "live_resolve requires l1_address"}
            params["l1_address"] = l1_address
        elif op in {"live_account", "live_active_orders", "live_recent_orders"}:
            resolved_account_index = account_index
            if resolved_account_index is not None:
                params["account_index"] = resolved_account_index
            if l1_address:
                params["l1_address"] = l1_address
            # When neither selector is provided, send no account param — the bot
            # uses its own configured Lighter account (not LIGHTER_API_KEY_INDEX,
            # which is a signing key index, not a Lighter account index).
        if op == "live_active_orders" and market_id is not None:
            params["market_id"] = market_id
        if op == "live_recent_orders":
            params["limit"] = limit
            if market_id is not None:
                params["market_id"] = market_id
            if cursor:
                params["cursor"] = cursor
        if op == "signals":
            params["limit"] = limit
            if market:
                params["market"] = market

        result = await call_bot(method, path, params=params)
        if "error" not in result:
            result = await enrich_perps_result(op, result)

        record(
            "mcp.tool.claudeburst.perps",
            op=op,
            elapsed_ms=monotonic_now() - t0,
        )
        return result

    @mcp.tool()
    async def lighter_history(
        market: str | None = None,
        limit: int = 50,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Per-tranche fill history for cost-basis and exit sequencing analysis.

        Prefer this over `claudeburst_perps(op="live_recent_orders")` when you need
        fills rather than order status. Use `market` to focus on one asset.
        """
        params: dict[str, Any] = {"limit": max(1, min(limit, 200))}
        if market:
            params["market"] = market
        if since:
            params["since"] = since
        t0 = monotonic_now()
        result = await call_bot("GET", "/account/history", params=params)
        record("mcp.tool.lighter.history", elapsed_ms=monotonic_now() - t0)
        return result

    @mcp.tool()
    async def lighter_shadow_create(
        market: str,
        side: str,
        trigger_price: float,
        trigger_type: str,
        size_usdc: float,
        order_type: str = "take_profit",
        review_window_seconds: int = 30,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a bot-managed shadow exit order for live position protection."""
        body = {
            "market": market,
            "side": side,
            "trigger_price": trigger_price,
            "trigger_type": trigger_type,
            "size_usdc": size_usdc,
            "order_type": order_type,
            "review_window_seconds": review_window_seconds,
        }
        if notes:
            body["notes"] = notes
        t0 = monotonic_now()
        result = await call_bot("POST", "/shadow-orders", body=body)
        record("mcp.tool.lighter.shadow.create", elapsed_ms=monotonic_now() - t0)
        return result

    @mcp.tool()
    async def lighter_shadow_list(
        status: str | None = None,
        market: str | None = None,
    ) -> dict[str, Any]:
        """List active or historical shadow orders to inspect the exit ladder."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if market:
            params["market"] = market
        t0 = monotonic_now()
        result = await call_bot("GET", "/shadow-orders", params=params)
        record("mcp.tool.lighter.shadow.list", elapsed_ms=monotonic_now() - t0)
        return result

    @mcp.tool()
    async def lighter_shadow_update(
        order_id: str,
        trigger_price: float | None = None,
        size_usdc: float | None = None,
        review_window_seconds: int | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Adjust an existing shadow order without recreating the whole ladder."""
        body: dict[str, Any] = {}
        if trigger_price is not None:
            body["trigger_price"] = trigger_price
        if size_usdc is not None:
            body["size_usdc"] = size_usdc
        if review_window_seconds is not None:
            body["review_window_seconds"] = review_window_seconds
        if notes is not None:
            body["notes"] = notes
        if not body:
            return {"error": "Provide at least one field to update"}
        t0 = monotonic_now()
        result = await call_bot("PATCH", f"/shadow-orders/{order_id}", body=body)
        record("mcp.tool.lighter.shadow.update", elapsed_ms=monotonic_now() - t0)
        return result

    @mcp.tool()
    async def lighter_shadow_cancel(order_id: str) -> dict[str, Any]:
        """Cancel a shadow order when the exit plan changes or is no longer needed."""
        t0 = monotonic_now()
        result = await call_bot("DELETE", f"/shadow-orders/{order_id}")
        record("mcp.tool.lighter.shadow.cancel", elapsed_ms=monotonic_now() - t0)
        return result

    @mcp.tool()
    async def lighter_shadow_log(order_id: str) -> dict[str, Any]:
        """Read the full audit trail for one shadow order after a trigger or abort."""
        t0 = monotonic_now()
        result = await call_bot("GET", f"/shadow-orders/{order_id}/log")
        record("mcp.tool.lighter.shadow.log", elapsed_ms=monotonic_now() - t0)
        return result
