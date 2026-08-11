"""manage.sock JSON-RPC client for the guarded reexec script.

Thin synchronous client deliberately outside the manage process so refuse/require
checks and post-boot whoami proof do not share the PID under replacement.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from transport_utils import MANAGE_SOCKET


def call_manage(
    method: str,
    params: dict[str, Any] | None = None,
    *,
    sock_path: str = MANAGE_SOCKET,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Invoke one JSON-RPC method on manage.sock; return result or error dict."""
    body = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or {},
        "id": 1,
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(sock_path)
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
            "error": f"manage.sock not found at {sock_path}",
        }
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "reason": "manage_sock_call_failed",
            "error": str(exc),
        }
    if "error" in raw:
        err = raw["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"status": "error", "reason": "manage_rpc_error", "error": message}
    result = raw.get("result", raw)
    return result if isinstance(result, dict) else {"status": "ok", "result": result}
