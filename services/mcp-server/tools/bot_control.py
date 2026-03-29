"""Perps bot lifecycle control — kill, pause, resume, health, live_resolve.

Isolated from the lighter tool as a safety gate for mutating operations.
Routes to the bot REST API via claudeburst_perps_common.call_bot.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_OPS: dict[str, tuple[str, str]] = {
    "kill": ("POST", "/commands/kill-switch"),
    "pause": ("POST", "/commands/pause"),
    "resume": ("POST", "/commands/resume"),
    "health": ("GET", "/health"),
    "live_resolve": ("GET", "/live/resolve-account"),
}

_OP_REQUIRED: dict[str, list[str]] = {
    "kill": [],
    "pause": [],
    "resume": [],
    "health": [],
    "live_resolve": ["l1_address"],
}


def register_bot_control_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    async def bot_control(op: str, arguments: str = "{}") -> dict[str, Any]:
        """Perps bot lifecycle control — kill, pause, resume, health check.

        CRITICAL: kill/pause/resume are MUTATING operations that affect live trading.
        For exchange data, positions, orders, balance, shadow orders — use lighter.

        Ops:
          kill         — activate kill switch, halt all trading (DESTRUCTIVE)
          pause        — pause scan loop
          resume       — resume scan loop
          health       — bot liveness check
          live_resolve — resolve account_index from l1_address (l1_address REQUIRED)
        """
        from .claudeburst_perps_common import call_bot, enrich_perps_result

        t0 = monotonic_now()

        try:
            args = json.loads(arguments)
            if not isinstance(args, dict):
                return {
                    "error": f"arguments must be a JSON object, got {type(args).__name__}"
                }
        except json.JSONDecodeError as exc:
            return {"error": f"Invalid arguments JSON: {exc}"}

        if op not in _OPS:
            ops_list = ", ".join(sorted(_OPS))
            return {"error": f"Unknown op: {op!r}. Available: {ops_list}"}

        required = _OP_REQUIRED[op]
        missing = [k for k in required if k not in args]
        if missing:
            return {"error": f"op={op!r} requires: {', '.join(missing)}"}

        method, path = _OPS[op]
        params: dict[str, Any] = {}
        if op == "live_resolve":
            params["l1_address"] = args["l1_address"]

        result = await call_bot(method, path, params=params)
        if "error" not in result and op == "health":
            result = await enrich_perps_result("health", result)

        duration = monotonic_now() - t0
        record("mcp.bot.control.dispatched", op=op, duration_s=round(duration, 3))
        return result
