"""Host reads and event queries for the Grok Bot egress watch loop."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scripts.model_manager.ui.controller.grokbot_egress_watch_logic import (
    GrokbotEgressSnapshot,
    _LOADER_STUCK_AFTER_TOOLS_LIST_S,
    watch_connection_state,
)

DEFAULT_DESKTOP_STATUS = Path.home() / ".gateway/grok-io/desktop-status.json"
DEFAULT_DAEMON_LOG = Path.home() / ".gateway/grok-io/sand-data/local-exec-daemon.log"
DEFAULT_DESKTOP_BINARY = Path("/opt/Grok Bot/grok-bot")
GC1_CLIENT_IP = "172.18.0.1"
HM1_CLIENT_IPS = ("10.81.0.166",)
STAGE_MANAGER_THREAD = "15851"
GC1_BUS_AGENT = "grok-bot-gc1"
MCP_CODE_PREFIX = "/mcp/code"
MCP_LIFE_PREFIX = "/mcp/life"


def read_desktop_status(path: Path = DEFAULT_DESKTOP_STATUS) -> dict[str, object] | None:
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


def open_gc1_bus_turn(*, thread: str = STAGE_MANAGER_THREAD) -> bool:
    """True when thread ``thread`` has an unfinished turn from grok-bot-gc1."""
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
            SELECT 1 FROM turns
            WHERE thread = ?
              AND from_agent = ?
              AND status = 'open'
            LIMIT 1
            """,
            (thread, GC1_BUS_AGENT),
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return row is not None


def query_events_rows(
    workspace_root: Path,
    sql: str,
    *params: str | int,
) -> list[dict[str, object]]:
    script = workspace_root / "scripts" / "query-events"
    if not script.is_file():
        raise RuntimeError(f"query-events not found: {script}")
    cmd = [
        str(script),
        "--sql",
        sql,
        *sum((["--sql-param", str(p)] for p in params), []),
        "--limit",
        "10000",
        "--compact",
    ]
    socket_path = os.environ.get("EVENTS_QUERY_SOCK")
    if socket_path:
        cmd.extend(["--socket", socket_path])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "query-events failed"
        raise RuntimeError(err)
    from deploy_identity.mcp_external_probe import json_object_from_mixed_stdout

    payload = json_object_from_mixed_stdout(proc.stdout or "")
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
    if gc1.tools_call_last_ms is not None and gc1.tools_call_last_ms >= gc1.tools_list_last_ms:
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
    """Open gc1 undici MCP on code/life surfaces (started without completed)."""
    open_started: dict[str, int] = {}
    for row in rows:
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
        path = _payload_str(row, "path")
        if path and not (
            path.startswith(MCP_CODE_PREFIX) or path.startswith(MCP_LIFE_PREFIX)
        ):
            if signal == "mcp.transport.request.started":
                path = _payload_str(row, "method")
        req_id = _payload_str(row, "jsonrpc_id") or f"{ts}"
        if signal == "mcp.transport.request.started":
            open_started[req_id] = ts
        elif req_id in open_started:
            del open_started[req_id]
    if not open_started:
        return False
    # Treat long-running calls as still open; stale ids fall out after 10 min.
    stale_cutoff = now_ms - 600_000
    return any(started >= stale_cutoff for started in open_started.values())


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
            _mcp_activity_since(event_rows, client_ip=ip, floor_ms=lookback_ms).tools_call_last_ms
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
        open_bus_turn = open_gc1_bus_turn()

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
    "GC1_CLIENT_IP",
    "build_snapshot_from_inputs",
    "desktop_pid_alive",
    "in_flight_gc1_mcp_from_rows",
    "loader_stuck_from_rows",
    "open_gc1_bus_turn",
    "query_events_rows",
    "read_daemon_log",
    "read_desktop_status",
]
