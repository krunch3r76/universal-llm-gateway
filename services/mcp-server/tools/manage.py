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
_PROGRESS_PROBE_TIMEOUT = 5.0

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
            return {
                "error": f"Manage API call timed out after {timeout:.0f}s",
                "_timeout": True,
            }
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
        result = {"error": msg}
        if raw.get("_timeout"):
            result["timed_out"] = True
        return result
    return raw.get("result", raw)


def _probe_manage(method: str, *, service: str = "") -> dict[str, Any]:
    """Best-effort follow-up probe used after long-running rebuild timeouts."""
    params: dict[str, Any] = {}
    if service:
        params["service"] = service
    raw = _call_manage(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout=_PROGRESS_PROBE_TIMEOUT,
    )
    return _extract_result(raw)


def _build_info_from_status(status_result: dict[str, Any]) -> dict[str, Any]:
    """Extract gateway build info from a status result when present."""
    build_info = status_result.get("build", {})
    return build_info if isinstance(build_info, dict) else {}


def _service_status_from_probes(
    status_result: dict[str, Any], health_result: dict[str, Any], service: str
) -> str:
    """Resolve a service status string from health/status probe results."""
    if "error" not in health_result:
        value = str(health_result.get("status", "")).strip()
        if value:
            return value
    if "error" not in status_result:
        services = status_result.get("services", {})
        if isinstance(services, dict):
            return str(services.get(service, "")).strip()
    return ""


def _lifecycle_timeout_result(action: str, service: str, timeout: float) -> dict[str, Any]:
    """Return a progress-oriented result after a long lifecycle call outlives its socket budget."""
    status_result = _probe_manage("status")
    health_result = _probe_manage("health", service=service)

    build_info = _build_info_from_status(status_result)
    service_status = _service_status_from_probes(status_result, health_result, service)
    action_label = action.replace("_", " ")
    action_title = action_label.capitalize()

    build_running = bool(build_info.get("running"))
    image_status = str(build_info.get("image_status", "")).strip()
    if action == "rebuild" and (build_running or image_status == "building"):
        return {
            "status": "in_progress",
            "service": service,
            "timed_out": True,
            "message": (
                f"{action_title} is still running after {timeout:.0f}s. "
                "The client timed out, but manage.sock reports the build is active."
            ),
            "build": build_info,
            "health": health_result,
            "next_step": (
                f"Call manage_service(action='wait_healthy', service='{service}', timeout=120) "
                "or poll health/status."
            ),
        }
    if service_status == "running":
        return {
            "status": "ok",
            "service": service,
            "timed_out": True,
            "message": (
                f"{action_title} exceeded the {timeout:.0f}s client budget, "
                f"but {service} is running now."
            ),
            "build": build_info,
            "health": health_result,
        }
    if service_status == "unhealthy":
        return {
            "status": "in_progress",
            "service": service,
            "timed_out": True,
            "message": (
                f"{action_title} exceeded the {timeout:.0f}s client budget. "
                f"manage.sock reports {service} as unhealthy/starting, so the operation may still be settling."
            ),
            "build": build_info,
            "health": health_result,
            "next_step": (
                f"Call manage_service(action='wait_healthy', service='{service}', timeout=120) "
                "or poll health/status."
            ),
        }
    return {
        "error": f"Manage API call timed out after {timeout:.0f}s",
        "timed_out": True,
        "service": service,
        "build": build_info,
        "health": health_result,
    }


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
          rebuild      — Rebuild container image and restart (gateway, mcp, event_service, cortex_api, agent_bus)
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
            timeout: Client wait budget in seconds for long-running actions
                (`wait_healthy`, `start`, `restart`, `rebuild`). On timeout,
                the tool returns structured progress when possible.

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

        # Long-running actions hold the connection open until done; extend socket timeout.
        sock_timeout = (
            timeout + _WAIT_HEALTHY_BUFFER
            if action in {"wait_healthy", "start", "restart", "rebuild"}
            else _DEFAULT_TIMEOUT
        )

        raw = _call_manage(
            {"jsonrpc": "2.0", "method": action, "params": params, "id": 1},
            timeout=sock_timeout,
        )
        timed_out = bool(raw.get("_timeout"))
        result = _extract_result(raw)
        if timed_out and action in {"start", "restart", "rebuild"} and service:
            result = _lifecycle_timeout_result(action, service, timeout)

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
