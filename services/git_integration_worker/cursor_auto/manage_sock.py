"""Thin manage.sock client for cursor-auto in-seat propagation execution."""

from __future__ import annotations

import json
import socket
from typing import Any

from transport_utils import MANAGE_SOCKET

_DEFAULT_TIMEOUT = 120.0


def call_manage(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Invoke one JSON-RPC method on manage.sock and return the result dict."""
    body = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }
    try:
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
    except FileNotFoundError:
        return {
            "status": "error",
            "reason": "manage_sock_missing",
            "error": f"manage.sock not found at {MANAGE_SOCKET}",
        }
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"status": "error", "reason": "manage_sock_call_failed", "error": str(exc)}
    if "error" in raw:
        err = raw["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"status": "error", "reason": "manage_rpc_error", "error": message}
    result = raw.get("result", raw)
    return result if isinstance(result, dict) else {"status": "ok", "result": result}


def sync_restart_service(service: str, *, reason: str = "") -> dict[str, Any]:
    """Request drain-gated sync_restart for one service slug."""
    params: dict[str, Any] = {"service": service}
    if reason:
        params["reason"] = reason
    return call_manage("sync_restart", params)


__all__ = ["call_manage", "sync_restart_service"]
