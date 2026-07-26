"""``charter_reload`` via manage.sock JSON-RPC."""

from __future__ import annotations

import json
import socket
from typing import Any

from transport_utils import MANAGE_SOCKET


def charter_reload(*, timeout: float = 30.0) -> dict[str, Any]:
    """Invoke manage.sock ``charter_reload`` and return the JSON-RPC result envelope."""
    body = {
        "jsonrpc": "2.0",
        "method": "charter_reload",
        "params": {},
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
