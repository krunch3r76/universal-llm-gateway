"""Shared restart-window probe for MCP tool ConnectError annotation.

MCP-alive: manage.sock ``busy_status`` is the primary read path.
Fallback: pinned ``/home/mcp/.gateway/restart-intents.db`` (compose mount).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from transport_utils import MANAGE_SOCKET
from universal_logging import get_logger

logger = get_logger(__name__)

_PINNED_GATEWAY_DB = Path("/home/mcp/.gateway/restart-intents.db")
_RETRY_AFTER_S = 30
_BUSY_STATUS_TIMEOUT_S = 2.0


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _annotation_from_window_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "restart_in_progress": True,
        "retry_after_s": _RETRY_AFTER_S,
        "window_deadline": row["deadline_at"],
        "window_id": row["window_id"],
        "window_scope": row["scope"],
    }


def _window_from_pinned_db(service: str) -> dict[str, Any] | None:
    db_path = _PINNED_GATEWAY_DB
    if not db_path.is_file():
        return None
    now = datetime.now(UTC)
    try:
        conn = sqlite3.connect(db_path, timeout=2.0)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM restart_windows WHERE state='open' ORDER BY opened_at"
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.debug("pinned restart-window db read failed: %s", exc)
        return None

    for row in rows:
        deadline = _parse_ts(row["deadline_at"])
        if deadline <= now:
            continue
        try:
            service_set = json.loads(row["service_set"])
        except (TypeError, json.JSONDecodeError):
            continue
        if service in service_set:
            return _annotation_from_window_row(row)
    return None


def _window_from_busy_status(service: str) -> dict[str, Any] | None:
    import socket

    body = {
        "jsonrpc": "2.0",
        "method": "busy_status",
        "params": {},
        "id": 1,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_BUSY_STATUS_TIMEOUT_S)
            sock.connect(MANAGE_SOCKET)
            sock.sendall(json.dumps(body).encode() + b"\n")
            data = b""
            while True:
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
        raw = json.loads(data.strip())
    except (OSError, json.JSONDecodeError, TimeoutError) as exc:
        logger.debug("manage.sock busy_status probe failed: %s", exc)
        return None

    result = raw.get("result", raw)
    if not isinstance(result, dict):
        return None

    windows = result.get("restart_windows")
    if isinstance(windows, dict):
        for entry in windows.get("open", []):
            if not isinstance(entry, dict):
                continue
            service_set = entry.get("service_set", [])
            if service in service_set:
                return {
                    "restart_in_progress": True,
                    "retry_after_s": entry.get("retry_after_s", _RETRY_AFTER_S),
                    "window_deadline": entry.get("deadline_at"),
                    "window_id": entry.get("window_id"),
                    "window_scope": entry.get("scope"),
                }

    services = result.get("services", {})
    if isinstance(services, dict):
        entry = services.get(service, {})
        if isinstance(entry, dict):
            view = entry.get("restart_window")
            if isinstance(view, dict) and view.get("state") == "open":
                return {
                    "restart_in_progress": True,
                    "retry_after_s": view.get("retry_after_s", _RETRY_AFTER_S),
                    "window_deadline": view.get("deadline_at"),
                    "window_id": view.get("window_id"),
                    "window_scope": view.get("scope"),
                }
    return None


def probe_restart_window(service: str) -> dict[str, Any] | None:
    """Return restart-window annotation fields when *service* is under maintenance."""
    annotation = _window_from_busy_status(service)
    if annotation is not None:
        return annotation
    return _window_from_pinned_db(service)


def annotate_unreachable_error(
    *,
    code: str,
    message: str,
    service: str = "stargate",
    flat_error: bool = False,
) -> dict[str, Any]:
    """Build a ConnectError payload, annotating when an operator window is open."""
    annotation = probe_restart_window(service)
    if annotation is None:
        if flat_error:
            return {"error": message}
        return {"error": {"code": code, "message": message}}

    if flat_error:
        return {"error": message, **annotation}

    return {
        "error": {
            "code": code,
            "message": message,
            **annotation,
        }
    }
