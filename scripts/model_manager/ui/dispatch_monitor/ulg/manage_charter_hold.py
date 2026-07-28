"""``charter_pause`` / ``charter_resume`` / ``charter_hold_status`` via manage.sock."""

from __future__ import annotations

import json
import socket
from typing import Any

from transport_utils import MANAGE_SOCKET


def _call(
    method: str, params: dict[str, Any] | None = None, *, timeout: float = 30.0
) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
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
    if "error" in raw:
        err = raw["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": message}
    return raw.get("result", raw)


def charter_pause(
    *,
    reason: str = "",
    set_by: str = "dispatch_monitor",
    timeout: float = 1830.0,
) -> dict[str, Any]:
    """Arm durable tick hold and wait for charter dispatches to drain."""
    params: dict[str, Any] = {"set_by": set_by, "timeout": max(0.0, timeout - 30.0)}
    if reason:
        params["reason"] = reason
    return _call("charter_pause", params, timeout=timeout)


def charter_resume(*, timeout: float = 30.0) -> dict[str, Any]:
    """Clear durable tick hold via manage.sock."""
    return _call("charter_resume", timeout=timeout)


def charter_hold_status(*, timeout: float = 30.0) -> dict[str, Any]:
    """Report durable hold + safe_to_quit via manage.sock."""
    return _call("charter_hold_status", timeout=timeout)
