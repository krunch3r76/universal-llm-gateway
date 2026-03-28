"""MCP tool: lighter_trades — query the Lighter perps trade log.

Read-only access to perps_trades.db written by the Lighter bot.
Operations: status, health, trades, signals, pnl, positions, logs.

DB path: /data/project/claudeburst/data/perps_trades.db
(host: /mnt/torus/projects/claudeburst/data/perps_trades.db)
"""

# TODO: Migrate status/positions/pnl queries to claudeburst_perps REST API
# once perps bot REST surface is live. Historical trades/signals may remain
# as SQLite queries.

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.getenv("PROJECT_ROOT", "/data/project")
_DATA_DIR = os.path.join(_PROJECT_ROOT, "claudeburst/data")
_DB_PATH = os.path.join(_DATA_DIR, "perps_trades.db")
_LOG_PATH = os.path.join(_DATA_DIR, "perps_bot.log")


def _connect() -> sqlite3.Connection | None:
    if not os.path.exists(_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _fetchall(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _fetchone(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def _op_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Latest heartbeat + open positions + session PnL + signal count."""
    heartbeat = _fetchone(conn, "SELECT * FROM heartbeats ORDER BY id DESC LIMIT 1")
    open_trades = _fetchall(conn, "SELECT * FROM trades WHERE status='open' ORDER BY id DESC")
    closed = _fetchall(conn, "SELECT * FROM trades WHERE status='closed'")
    recent_signals = _fetchall(conn, "SELECT * FROM signals ORDER BY id DESC LIMIT 5")
    session_pnl = sum(t.get("pnl_usdc") or 0.0 for t in closed)

    return {
        "heartbeat": heartbeat,
        "open_positions": open_trades,
        "session_pnl": round(session_pnl, 2),
        "total_trades": len(open_trades) + len(closed),
        "total_signals": conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0],
        "recent_signals": recent_signals,
    }


def _op_trades(conn: sqlite3.Connection, limit: int, status: str) -> dict[str, Any]:
    if status == "all":
        rows = _fetchall(conn, "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
    else:
        rows = _fetchall(
            conn,
            "SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ?",
            (status, limit),
        )
    return {"trades": rows, "count": len(rows)}


def _op_signals(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    rows = _fetchall(conn, "SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,))
    return {"signals": rows, "count": len(rows)}


def _op_pnl(conn: sqlite3.Connection) -> dict[str, Any]:
    closed = _fetchall(conn, "SELECT * FROM trades WHERE status='closed'")
    if not closed:
        return {"total_pnl": 0.0, "trade_count": 0, "win_rate": 0.0, "by_strategy": {}, "by_symbol": {}}

    total_pnl = sum(t.get("pnl_usdc") or 0.0 for t in closed)
    wins = sum(1 for t in closed if (t.get("pnl_usdc") or 0.0) > 0)
    win_rate = wins / len(closed) if closed else 0.0

    by_strategy: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    for t in closed:
        pnl = t.get("pnl_usdc") or 0.0
        by_strategy[t["signal_type"]] = by_strategy.get(t["signal_type"], 0.0) + pnl
        by_symbol[t["symbol"]] = by_symbol.get(t["symbol"], 0.0) + pnl

    return {
        "total_pnl": round(total_pnl, 2),
        "trade_count": len(closed),
        "win_rate": round(win_rate, 4),
        "wins": wins,
        "losses": len(closed) - wins,
        "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
        "by_symbol": {k: round(v, 2) for k, v in by_symbol.items()},
    }


def _op_positions(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = _fetchall(conn, "SELECT * FROM trades WHERE status='open' ORDER BY id DESC")
    return {"positions": rows, "count": len(rows)}


def _op_health(conn: sqlite3.Connection) -> dict[str, Any]:
    last_hb = _fetchone(conn, "SELECT * FROM heartbeats ORDER BY id DESC LIMIT 1")
    if last_hb is None:
        return {"alive": False, "reason": "No heartbeats recorded"}

    startup = _fetchone(
        conn,
        "SELECT timestamp FROM heartbeats WHERE prices_json LIKE '%_startup%' ORDER BY id DESC LIMIT 1",
    )

    last_ts = last_hb["timestamp"]
    try:
        last_dt = datetime.fromisoformat(last_ts)
        age_s = (datetime.now(UTC) - last_dt).total_seconds()
    except (ValueError, TypeError):
        age_s = 999.0

    alive = age_s < 120.0
    uptime_s = 0
    if startup:
        try:
            start_dt = datetime.fromisoformat(startup["timestamp"])
            uptime_s = int((datetime.now(UTC) - start_dt).total_seconds())
        except (ValueError, TypeError):
            pass

    return {
        "alive": alive,
        "last_heartbeat": last_ts,
        "heartbeat_age_s": round(age_s, 1),
        "equity": last_hb.get("equity"),
        "total_exposure": last_hb.get("total_exposure"),
        "open_positions": last_hb.get("open_positions"),
        "halted": bool(last_hb.get("halted")),
        "uptime_s": uptime_s,
    }


def _op_logs(lines: int) -> dict[str, Any]:
    if not os.path.exists(_LOG_PATH):
        return {"error": f"Log file not found: {_LOG_PATH}"}
    try:
        with open(_LOG_PATH, "rb") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        text = b"".join(tail).decode("utf-8", errors="replace")
        return {"lines": len(tail), "log": text}
    except OSError as exc:
        return {"error": f"Failed to read log: {exc}"}


def register_lighter_trades_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def lighter_trades(
        op: str = "status",
        limit: int = 20,
        status: str = "all",
        lines: int = 50,
    ) -> dict[str, Any]:
        """Query the Lighter perps trade log — visibility into what the bot is doing.

        Use this FIRST when asked about bot performance, open positions, or signals.

        Operations:
          status    — latest heartbeat + open positions + session PnL + recent signals
                      (the "show me what's happening" command)
          health    — is the bot alive? last heartbeat, equity, uptime
                      (quick liveness check — if last heartbeat >60s ago, bot is dead)
          trades    — recent trades, filterable by status (open/closed/all)
          signals   — recent signals with action taken (executed/risk_blocked)
          pnl       — aggregated PnL: total, by strategy, by symbol, win rate
          positions — all open positions with entry details
          logs      — tail last N lines of bot log file (lines param, default 50)
        """
        t0 = monotonic_now()

        if op == "logs":
            result = _op_logs(lines)
            duration = monotonic_now() - t0
            record("mcp.lighter.trades.queried", op=op, duration_s=round(duration, 3))
            return result

        conn = _connect()
        if conn is None:
            record("mcp.lighter.trades.unavailable", op=op)
            return {"error": f"Trade log DB not found at {_DB_PATH}"}

        try:
            if op == "status":
                result = _op_status(conn)
            elif op == "health":
                result = _op_health(conn)
            elif op == "trades":
                result = _op_trades(conn, limit, status)
            elif op == "signals":
                result = _op_signals(conn, limit)
            elif op == "pnl":
                result = _op_pnl(conn)
            elif op == "positions":
                result = _op_positions(conn)
            else:
                result = {
                    "error": f"Unknown op: {op}. Use: status, health, trades, signals, pnl, positions, logs"
                }
        except sqlite3.Error as exc:
            record("mcp.lighter.trades.error", op=op, error=str(exc))
            return {"error": f"Query failed: {exc}"}
        finally:
            conn.close()

        duration = monotonic_now() - t0
        record("mcp.lighter.trades.queried", op=op, duration_s=round(duration, 3))
        return result
