"""Read-only JSON-RPC client for the manage socket.

Slice 1 of the live-verify gate probes ``fleet_liveness`` on the running
manage process. This module sends that one method. It does not expose
restart, stop, start, or rebuild.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from transport_utils import MANAGE_SOCKET

_READONLY_METHOD = "fleet_liveness"


def fleet_liveness(
    *,
    code_ref: str,
    services: list[str],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call manage ``fleet_liveness`` and return the result object.

    Transport failure and JSON-RPC error both return ``{"error": ...}``.
    Callers fold that into a not-live relation; they do not raise a fifth
    close token.
    """
    return call_readonly(
        _READONLY_METHOD,
        {"code_ref": code_ref, "services": services},
        timeout=timeout,
    )


def call_readonly(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send one read-only JSON-RPC 2.0 request. Non-fleet methods never connect."""
    if method != _READONLY_METHOD:
        return {"error": f"read-only manage client refuses method {method!r}"}
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    raw = _exchange(body, timeout=timeout)
    if "error" in raw and "result" not in raw:
        err = raw["error"]
        message = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": message}
    result = raw.get("result", raw)
    return result if isinstance(result, dict) else {"error": "non_object_result"}


def _exchange(body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
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
    return parsed
