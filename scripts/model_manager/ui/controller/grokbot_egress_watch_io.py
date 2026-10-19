"""Host reads and event queries for the Grok Bot egress watch loop."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from transport_utils.client_factory import DEFAULT_EVENTS_QUERY_SOCK_PATH

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    _LOADER_STUCK_AFTER_TOOLS_LIST_S,
    GrokbotEgressSnapshot,
    watch_connection_state,
)

DEFAULT_DESKTOP_STATUS = Path.home() / ".gateway/grok-io/desktop-status.json"
DEFAULT_HOP_COOLDOWN = DEFAULT_DESKTOP_STATUS.parent / "hop-cooldown.json"
DEFAULT_DAEMON_LOG = Path.home() / ".gateway/grok-io/sand-data/local-exec-daemon.log"
DEFAULT_DESKTOP_BINARY = Path("/opt/Grok Bot/grok-bot")
_PRESENCE_KEEPALIVE_TIMEOUT_S = 5.0


def keep_alive_grokbot_presence(
    display: str = ":9", cdp_port: int = 9339
) -> dict[str, Any]:
    """Non-disruptive X11 + CDP presence wake when Grok Bot is running."""
    from cursor_bridge.grok_computer import wake_grok_computer

    try:
        return asyncio.run(
            wake_grok_computer(
                cdp_port=cdp_port,
                display=display,
                timeout_s=_PRESENCE_KEEPALIVE_TIMEOUT_S,
            )
        )
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
GC1_CLIENT_IP = "172.18.0.1"
HM1_CLIENT_IPS = ("10.81.0.166",)
STAGE_MANAGER_THREAD = "15851"
GC1_BUS_AGENT = "grok-bot-gc1"
MCP_CODE_PREFIX = "/mcp/code"
MCP_LIFE_PREFIX = "/mcp/life"
IN_FLIGHT_STALE_MS = 600_000
# Dispatch-admit ack tips stop deferring after this age with no newer gc1 turn (r3 f3).
GC1_BUS_TIP_STALE_MS = 120_000
_UNDICI_EDGE_CORRELATION_MS = 2_000

_GC1_TERMINAL_SUBJECT_RES = (
    re.compile(r"^DONE(?:[\|\s]|$)", re.IGNORECASE),
    re.compile(r"^VERDICT(?:[\:\|\s]|$)", re.IGNORECASE),
    re.compile(r"^STATUS(?:[\|\s]|$)", re.IGNORECASE),
    re.compile(r"^ACK(?:[\|\s]|$)", re.IGNORECASE),
    re.compile(r"^CHECKPOINT(?:[\|\s]|$)", re.IGNORECASE),
    re.compile(r"^PICKUP(?:[\#\|\s]|$)", re.IGNORECASE),
    re.compile(r"^NUDGE(?:[\|\s]|$)", re.IGNORECASE),
    re.compile(r"^LANDED(?:[\|\s]|$)", re.IGNORECASE),
)
_GC1_TIME_BOUNDED_TERMINAL_RES = (
    re.compile(r"^cursor-sdk generate admitted", re.IGNORECASE),
)


def load_hop_cooldown_journal(
    path: Path = DEFAULT_HOP_COOLDOWN,
) -> list[dict[str, object]]:
    """Load persisted context-hop cooldown entries."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    entries: list[dict[str, object]] = []
    for item in parsed:
        if isinstance(item, dict):
            entries.append(item)
    return entries


def save_hop_cooldown_journal(
    path: Path, cooldown_until: dict[str, float]
) -> None:
    """Persist agent cooldown_until values for context-hop."""
    entries = [
        {"agent_id": agent_id, "cooldown_until": until}
        for agent_id, until in sorted(cooldown_until.items())
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")


def read_desktop_status(
    path: Path = DEFAULT_DESKTOP_STATUS,
) -> dict[str, object] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def desktop_pid_alive(status: dict[str, object] | None) -> tuple[bool, int | None]:
    if not status:
        return False, None
    pid_raw = status.get("pid")
    if not isinstance(pid_raw, int):
        return False, None
    try:
        os.kill(pid_raw, 0)
    except OSError:
        return False, pid_raw
    return True, pid_raw


def read_daemon_log(path: Path = DEFAULT_DAEMON_LOG) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _agent_bus_db_path() -> Path | None:
    raw = os.environ.get("AGENT_BUS_DB_PATH", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_file() else None
    host = Path.home() / ".agent-bus" / "messages.db"
    return host if host.is_file() else None


def _subject_is_terminal(subject: str) -> bool:
    subject_norm = subject.strip()
    if not subject_norm:
        return True
    return any(p.search(subject_norm) for p in _GC1_TERMINAL_SUBJECT_RES)


def _subject_time_bounded_terminal(
    subject: str, *, tip_created_ms: int | None, now_ms: int
) -> bool:
    subject_norm = subject.strip()
    if not subject_norm or tip_created_ms is None:
        return False
    if not any(p.search(subject_norm) for p in _GC1_TIME_BOUNDED_TERMINAL_RES):
        return False
    return (now_ms - tip_created_ms) >= GC1_BUS_TIP_STALE_MS


def gc1_bus_turn_in_flight(
    *,
    from_agent: str,
    subject: str,
    tip_created_ms: int | None = None,
    now_ms: int | None = None,
) -> bool:
    """True when the thread tip is a non-terminal grok-bot-gc1 turn still in play."""
    if from_agent != GC1_BUS_AGENT:
        return False
    if _subject_is_terminal(subject):
        return False
    if now_ms is not None and _subject_time_bounded_terminal(
        subject, tip_created_ms=tip_created_ms, now_ms=now_ms
    ):
        return False
    return True


def _parse_bus_created_ms(created_at: str) -> int | None:
    raw = (created_at or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _undici_edge_timestamps_ms(
    rows: list[dict[str, object]], *, floor_ms: int
) -> list[int]:
    """POST times for box-proxy undici hits on MCP code/life mounts."""
    stamps: list[int] = []
    for row in rows:
        ts = row.get("ts_unix_ms")
        if not isinstance(ts, int) or ts < floor_ms:
            continue
        if _payload_str(row, "signal") != "mcp.edge.request.observed":
            continue
        if _payload_str(row, "client_ip") != GC1_CLIENT_IP:
            continue
        if "undici" not in _payload_str(row, "user_agent").lower():
            continue
        path = _payload_str(row, "path")
        if not (path.startswith(MCP_CODE_PREFIX) or path.startswith(MCP_LIFE_PREFIX)):
            continue
        method = _payload_str(row, "method").upper()
        if method and method != "POST":
            continue
        stamps.append(ts)
    return stamps


def _gc1_box_transport_start(
    ts: int, undici_stamps: list[int], *, window_ms: int = _UNDICI_EDGE_CORRELATION_MS
) -> bool:
    """True when transport start correlates to box undici (not other io-host clients)."""
    if not undici_stamps:
        return False
    return any(abs(ts - edge_ts) <= window_ms for edge_ts in undici_stamps)


def open_gc1_bus_turn(
    *, thread: str = STAGE_MANAGER_THREAD, now_ms: int | None = None
) -> bool:
    """True when the latest bus turn on ``thread`` is in-flight work from gc1."""
    path = _agent_bus_db_path()
    if path is None:
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            """
            SELECT from_agent, subject, created_at FROM turns
            WHERE thread = ? AND from_agent != 'manage'
            ORDER BY turn_number DESC
            LIMIT 1
            """,
            (thread,),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    if row is None:
        return False
    from_agent = str(row[0] or "")
    subject = str(row[1] or "")
    tip_created_ms = _parse_bus_created_ms(str(row[2] or ""))
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return gc1_bus_turn_in_flight(
        from_agent=from_agent,
        subject=subject,
        tip_created_ms=tip_created_ms,
        now_ms=now_ms,
    )


def query_events_rows(
    workspace_root: Path,
    sql: str,
    *params: str | int,
    limit: int = 1000,
) -> list[dict[str, object]]:
    """Read Event Service rows over the query socket.

    ``workspace_root`` is unused. The manage loop still passes it so the call
    shape stays stable. A missing socket or an error envelope raises so the
    loop logs one warning and continues with no rows.
    """
    del workspace_root
    from event_store.query_client import query_sql

    socket_path = os.environ.get("EVENTS_QUERY_SOCK", DEFAULT_EVENTS_QUERY_SOCK_PATH)
    if not os.path.exists(socket_path):
        raise RuntimeError(f"events query socket missing: {socket_path}")
    payload = query_sql(
        sql,
        params=list(params) if params else None,
        limit=limit,
        url=f"unix://{socket_path}",
        timeout=10.0,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("events query returned a non-object payload")
    if payload.get("error"):
        raise RuntimeError(str(payload.get("error")))
    rows = payload.get("rows")
    if isinstance(rows, list):
        return rows
    return []


@dataclass(frozen=True)
class _McpActivity:
    tools_list_last_ms: int | None
    tools_call_last_ms: int | None
    init_last_ms: int | None
    undici_last_ms: int | None


def _row_payload(row: dict[str, object]) -> dict[str, object]:
    raw = row.get("payload")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _payload_str(row: dict[str, object], key: str) -> str:
    val = row.get(key)
    if val is None:
        val = _row_payload(row).get(key)
    return str(val) if val is not None else ""


def _mcp_activity_since(
    rows: list[dict[str, object]], *, client_ip: str, floor_ms: int
) -> _McpActivity:
    tools_list_last: int | None = None
    tools_call_last: int | None = None
    init_last: int | None = None
    undici_last: int | None = None
    for row in rows:
        if _payload_str(row, "client_ip") != client_ip:
            continue
        ts = row.get("ts_unix_ms")
        if not isinstance(ts, int) or ts < floor_ms:
            continue
        signal = _payload_str(row, "signal")
        mcp_method = _payload_str(row, "mcp_method")
        if signal in {
            "mcp.request.completed",
            "mcp.transport.request.completed",
        }:
            if mcp_method == "tools/list":
                tools_list_last = max(tools_list_last or 0, ts)
            elif mcp_method == "tools/call":
                tools_call_last = max(tools_call_last or 0, ts)
            elif mcp_method == "initialize":
                init_last = max(init_last or 0, ts)
        if signal == "mcp.edge.request.observed":
            ua = _payload_str(row, "user_agent")
            if "undici" in ua.lower():
                undici_last = max(undici_last or 0, ts)
    return _McpActivity(
        tools_list_last_ms=tools_list_last,
        tools_call_last_ms=tools_call_last,
        init_last_ms=init_last,
        undici_last_ms=undici_last,
    )


def loader_stuck_from_rows(
    rows: list[dict[str, object]], *, now_ms: int, floor_ms: int
) -> tuple[bool, float | None]:
    gc1 = _mcp_activity_since(rows, client_ip=GC1_CLIENT_IP, floor_ms=floor_ms)
    if gc1.tools_list_last_ms is None or gc1.init_last_ms is None:
        return False, None
    if (
        gc1.tools_call_last_ms is not None
        and gc1.tools_call_last_ms >= gc1.tools_list_last_ms
    ):
        return False, None
    since_s = (now_ms - gc1.tools_list_last_ms) / 1000.0
    if since_s < _LOADER_STUCK_AFTER_TOOLS_LIST_S:
        return False, None
    hm1_ok = any(
        _mcp_activity_since(rows, client_ip=ip, floor_ms=floor_ms).tools_call_last_ms
        is not None
        for ip in HM1_CLIENT_IPS
    )
    if not hm1_ok:
        return False, None
    return True, since_s


def in_flight_gc1_mcp_from_rows(
    rows: list[dict[str, object]], *, now_ms: int, floor_ms: int
) -> bool:
    """Open gc1 MCP calls: transport started paired to end by ``jsonrpc_id``.

    Rows without ``jsonrpc_id`` (GET class-B 405, notifications) are ignored —
    they must not defer recovery. Orphan started rows age out after
    ``IN_FLIGHT_STALE_MS`` so missing terminal events cannot defer past the
    deferral ceiling.
    """
    undici_stamps = _undici_edge_timestamps_ms(rows, floor_ms=floor_ms)
    open_by_id: dict[str, deque[int]] = {}
    ordered = sorted(
        (r for r in rows if isinstance(r.get("ts_unix_ms"), int)),
        key=lambda r: int(r["ts_unix_ms"]),  # type: ignore[arg-type]
    )
    for row in ordered:
        ts = row.get("ts_unix_ms")
        if not isinstance(ts, int) or ts < floor_ms:
            continue
        signal = _payload_str(row, "signal")
        if signal not in {
            "mcp.transport.request.started",
            "mcp.transport.request.completed",
            "mcp.transport.request.failed",
        }:
            continue
        if _payload_str(row, "client_ip") != GC1_CLIENT_IP:
            continue
        req_id = _payload_str(row, "jsonrpc_id")
        if signal == "mcp.transport.request.started":
            if not req_id:
                continue
            if not _gc1_box_transport_start(ts, undici_stamps):
                continue
            open_by_id.setdefault(req_id, deque()).append(ts)
        elif req_id and req_id in open_by_id and open_by_id[req_id]:
            open_by_id[req_id].popleft()
            if not open_by_id[req_id]:
                del open_by_id[req_id]
    stale_cutoff = now_ms - IN_FLIGHT_STALE_MS
    for started_ts in (ts for dq in open_by_id.values() for ts in dq):
        if started_ts >= stale_cutoff:
            return True
    return False


def build_snapshot_from_inputs(
    *,
    now_ms: int,
    desktop_running: bool,
    desktop_pid: int | None,
    daemon_log: str | None,
    health_http_status: int | None,
    event_rows: list[dict[str, object]] | None,
    open_bus_turn: bool | None = None,
) -> GrokbotEgressSnapshot:
    log_ok = daemon_log is not None
    connected, disconnected_s = (False, None)
    if daemon_log is not None:
        connected, disconnected_s = watch_connection_state(daemon_log, now_ms=now_ms)

    lookback_ms = now_ms - max(_LOADER_STUCK_AFTER_TOOLS_LIST_S * 1000, 600_000)
    loader_stuck = False
    loader_stuck_s: float | None = None
    undici_idle_s: float | None = None
    in_flight = False
    hm1_ok = False
    if event_rows is not None:
        loader_stuck, loader_stuck_s = loader_stuck_from_rows(
            event_rows, now_ms=now_ms, floor_ms=lookback_ms
        )
        gc1 = _mcp_activity_since(
            event_rows, client_ip=GC1_CLIENT_IP, floor_ms=lookback_ms
        )
        if gc1.undici_last_ms is not None:
            undici_idle_s = (now_ms - gc1.undici_last_ms) / 1000.0
        in_flight = in_flight_gc1_mcp_from_rows(
            event_rows, now_ms=now_ms, floor_ms=lookback_ms
        )
        hm1_ok = any(
            _mcp_activity_since(
                event_rows, client_ip=ip, floor_ms=lookback_ms
            ).tools_call_last_ms
            is not None
            for ip in HM1_CLIENT_IPS
        )

    tunnel_stale = (
        desktop_running
        and not connected
        and disconnected_s is not None
        and disconnected_s >= 60.0
    )

    if open_bus_turn is None:
        open_bus_turn = open_gc1_bus_turn(now_ms=now_ms)

    return GrokbotEgressSnapshot(
        desktop_running=desktop_running,
        desktop_pid=desktop_pid,
        watch_connected=connected,
        watch_disconnected_s=disconnected_s,
        daemon_log_readable=log_ok,
        health_http_status=health_http_status,
        tunnel_stale=tunnel_stale,
        loader_stuck=loader_stuck,
        loader_stuck_s=loader_stuck_s,
        hm1_tools_call_ok=hm1_ok,
        undici_idle_s=undici_idle_s,
        in_flight_gc1_mcp=in_flight,
        open_gc1_bus_turn=open_bus_turn,
    )


__all__ = [
    "DEFAULT_DAEMON_LOG",
    "DEFAULT_DESKTOP_BINARY",
    "DEFAULT_DESKTOP_STATUS",
    "DEFAULT_HOP_COOLDOWN",
    "load_hop_cooldown_journal",
    "save_hop_cooldown_journal",
    "keep_alive_grokbot_presence",
    "GC1_BUS_TIP_STALE_MS",
    "GC1_CLIENT_IP",
    "IN_FLIGHT_STALE_MS",
    "build_snapshot_from_inputs",
    "desktop_pid_alive",
    "gc1_bus_turn_in_flight",
    "in_flight_gc1_mcp_from_rows",
    "loader_stuck_from_rows",
    "open_gc1_bus_turn",
    "query_events_rows",
    "read_daemon_log",
    "read_desktop_status",
]
