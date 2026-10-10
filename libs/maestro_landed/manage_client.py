"""Read-only manage JSON-RPC for maestro-landed-live slice 1."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from typing import Any

from transport_utils import MANAGE_SOCKET

from maestro_landed.manage_allowlist import ALLOWED_MANAGE_METHODS, refuse_manage_call


@dataclass
class ManageClient:
    """Records every call for AC3 trace checks."""

    trace: list[dict[str, Any]] = field(default_factory=list)
    timeout: float = 30.0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        reason = refuse_manage_call(method, params)
        if reason:
            return {"error": reason, "refused": True}
        self.trace.append({"method": method, "params": dict(params)})
        return _exchange({"jsonrpc": "2.0", "method": method, "params": params, "id": 1}, timeout=self.timeout)


def _exchange(body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    method = body.get("method")
    if method not in ALLOWED_MANAGE_METHODS:
        return {"error": f"read-only manage client refuses method {method!r}"}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(MANAGE_SOCKET)
            sock.sendall(json.dumps(body).encode() + b"\n")
            data = b""
            while b"\n" not in data:
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                data += chunk
        if not data:
            return {"error": "empty manage response"}
        parsed = json.loads(data.strip().splitlines()[0])
    except FileNotFoundError:
        return {"error": f"manage socket not found: {MANAGE_SOCKET}"}
    except TimeoutError:
        return {"error": f"manage socket timed out after {timeout:.0f}s"}
    except ConnectionRefusedError as exc:
        return {"error": f"manage socket refused: {exc}"}
    except json.JSONDecodeError as exc:
        return {"error": f"malformed manage response: {exc}"}
    except OSError as exc:
        return {"error": f"manage socket failed: {exc}"}
    if not isinstance(parsed, dict):
        return {"error": "non_object manage response"}
    if "error" in parsed and "result" not in parsed:
        err = parsed["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": message}
    result = parsed.get("result", parsed)
    return result if isinstance(result, dict) else {"error": "non_object_result"}
