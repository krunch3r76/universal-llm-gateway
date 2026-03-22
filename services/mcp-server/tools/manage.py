"""Manage service tool — agent-driven service lifecycle via manage.sock.

Connects to /tmp/universal-protocol/manage.sock (JSON-RPC 2.0 over UDS).
Exposes start, stop, rebuild, restart, status, health, and wait_healthy
for gateway-managed services. Single entry point reduces agent context overhead.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

_MANAGE_SOCK = os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")
_DEFAULT_TIMEOUT = 30.0
_WAIT_HEALTHY_BUFFER = 30.0

_VALID_ACTIONS = frozenset(
    {"status", "health", "start", "stop", "restart", "rebuild", "wait_healthy"}
)


def _call_manage(
    body: dict[str, Any], *, timeout: float = _DEFAULT_TIMEOUT
) -> dict[str, Any]:
    """Send one JSON-RPC request to manage.sock and return the parsed response."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(_MANAGE_SOCK)
            sock.sendall(json.dumps(body).encode() + b"\n")
            data = b""
            while True:
                chunk = sock.recv(65_536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            return json.loads(data.strip())
        except FileNotFoundError:
            logger.error("Manage socket not found: %s", _MANAGE_SOCK, exc_info=True)
            return {
                "error": (
                    "manage.sock not found. Ensure ./manage is running (TUI or headless)."
                )
            }
        except TimeoutError:
            return {"error": f"Manage API call timed out after {timeout:.0f}s"}
        except ConnectionRefusedError as exc:
            return {"error": f"Manage API not reachable: {exc}"}
        except json.JSONDecodeError as exc:
            return {"error": f"Malformed response from manage API: {exc}"}
        except Exception as exc:
            logger.error("Manage API call failed unexpectedly: %s", exc, exc_info=True)
            return {"error": f"Manage API call failed: {exc}"}


def _extract_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Unwrap JSON-RPC envelope; surface error as {'error': ...}.

    Args:
        raw: JSON-RPC 2.0 response dict (result or error key).

    Returns:
        result dict on success, or {'error': message} when the response contains error.
    """
    if "error" in raw:
        err = raw["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return {"error": msg}
    return raw.get("result", raw)


def register_manage_tools(mcp: FastMCP) -> None:
    """Register the manage_service tool on the MCP server instance."""

    @mcp.tool()
    def manage_service(
        action: str,
        service: str = "",
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Manage gateway service lifecycle — start, stop, rebuild, restart, or check health.

        Connects to manage.sock (the ./manage process UDS API) and dispatches
        the requested lifecycle operation via JSON-RPC 2.0. Use action='status'
        to survey all services without specifying a service name.

        Actions:
          status       — Return running/stopped for core services from check_all (no service arg needed)
          health       — Return health detail for one service
          start        — Start a stopped service
          stop         — Stop a running service
          restart      — Stop then start a service
          rebuild      — Rebuild container image and restart (gateway, mcp, cortex_api, agent_bus)
          wait_healthy — Block until service is RUNNING or timeout (use after start/restart/rebuild)

        Services: gateway, stargate, rag, cloud_proxy, mcp, event_service,
        cortex_api, agent_bus

        Agent workflow after code changes:
          1. quality_gate(files=[...])
          2. manage_service(action="rebuild", service="gateway")
          3. manage_service(action="wait_healthy", service="gateway", timeout=120)
          4. pipeline_run(...)

        Args:
            action: Operation from the list above.
            service: Target service name (not needed for 'status').
            timeout: Seconds before wait_healthy gives up (default 120).

        Returns:
            On success: action-specific result dict
            On error: {"error": "<message>"}
        """
        if action not in _VALID_ACTIONS:
            return {
                "error": (
                    f"Unknown action: '{action}'. "
                    f"Valid: {', '.join(sorted(_VALID_ACTIONS))}"
                )
            }

        t0 = monotonic_now()
        event_params: dict[str, str | float] = {"action": action}
        if service:
            event_params["service"] = service
        record("mcp.manage.service.called", **event_params)

        params: dict[str, Any] = {}
        if service:
            params["service"] = service
        if action == "wait_healthy":
            params["timeout"] = timeout

        # wait_healthy holds the connection open until done; extend socket timeout.
        sock_timeout = (
            timeout + _WAIT_HEALTHY_BUFFER
            if action == "wait_healthy"
            else _DEFAULT_TIMEOUT
        )

        raw = _call_manage(
            {"jsonrpc": "2.0", "method": action, "params": params, "id": 1},
            timeout=sock_timeout,
        )
        result = _extract_result(raw)

        duration = monotonic_now() - t0
        if "error" in result:
            record(
                "mcp.manage.service.failed",
                action=action,
                service=service,
                error=result["error"],
                duration_s=round(duration, 3),
            )
        else:
            record(
                "mcp.manage.service.completed",
                action=action,
                service=service,
                duration_s=round(duration, 3),
            )

        return result
