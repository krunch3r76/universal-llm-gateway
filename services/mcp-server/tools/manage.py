"""Manage service tool — agent-driven service lifecycle via manage.sock.

Connects to /tmp/universal-protocol/manage.sock (JSON-RPC 2.0 over UDS).
Exposes start, stop, rebuild, restart, status, health, and wait_healthy
for gateway-managed services. Single entry point reduces agent context overhead.
"""

from __future__ import annotations

import json
import os
import socket
from typing import TYPE_CHECKING, Any

from mcp_events import monotonic_now, record
from universal_logging import get_logger

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = get_logger(__name__)

_MANAGE_SOCK = os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")
_DEFAULT_TIMEOUT = 30.0
_WAIT_HEALTHY_BUFFER = 30.0
_PROGRESS_PROBE_TIMEOUT = 5.0

_VALID_ACTIONS = frozenset(
    {
        "status",
        "health",
        "start",
        "stop",
        "restart",
        "sync_restart",
        "rebuild",
        "wait_healthy",
    }
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
            # Common when ./manage TUI is not running; error message guides user.
            # Do not log as ERROR (per quality gates — use event instead).
            logger.warning("Manage socket not found: %s", _MANAGE_SOCK)
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
            logger.warning("Manage API call failed unexpectedly: %s", exc)
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


def _lifecycle_timeout_result(
    action: str, service: str, timeout: float
) -> dict[str, Any]:
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
                f"Call manage(action='wait_healthy', service='{service}', timeout=120) "
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
                f"Call manage(action='wait_healthy', service='{service}', timeout=120) "
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
    """Register the manage tool on the MCP server instance."""

    @mcp.tool(title="Manage Services")
    def manage(
        action: str,
        service: str = "",
        timeout: float = 120.0,
        force: bool = False,
    ) -> dict[str, Any]:
        """Service lifecycle — start, stop, restart, sync_restart, rebuild, health, wait_healthy.

        action: lifecycle action (see table below)
        service: service name (required for most actions)
        timeout: seconds to wait for wait_healthy (default 120)
        force: bypass the drain check for stop/restart/sync_restart (default False)

        Actions:
          status        (no service needed) — running/stopped for all services
          health        (service)           — health detail for one service
          start         (service)           — start a stopped service
          stop          (service)           — stop a running service
          restart       (service)           — stop then start (no source sync)
          sync_restart  (service)           — deploy local code edits. gateway
                                             uses bind-mounts (restart only); mcp
                                             does a cached refresh-source rebuild
                                             (~20s); host procs just restart.
          rebuild       (service)           — full --no-cache rebuild. Forbidden
                                             for gateway/mcp via this tool — use
                                             sync_restart. Ops-only via TUI.
          wait_healthy  (service, timeout?) — block until RUNNING or timeout

        Services: gateway, stargate, rag, cloud_proxy, mcp, event_service,
                  cortex_api, agent_bus, email_bridge, grokbuild_worker

        Drain gate: stop/restart/sync_restart are deferred when the target has
        in-flight work (e.g. a running async pipeline on stargate, a build on
        grokbuild_worker). A deferral returns
        {"status":"deferred","state","service","reason","retry_after_s",
        "active_work"} where state ∈ {busy, in_progress, probe_error}. Honor
        retry_after_s and retry, or pass force=true to preempt in-flight work.

        Self-restart caveat: sync_restart(service="mcp") returns
        "rebuild_scheduled" then recreates the container in background. During
        the window, calls may get -32099 with data.reason="server_restarting"
        and Retry-After: 30. Always follow with wait_healthy.
        """
        if action == "rebuild" and service in {"gateway", "mcp"}:
            heavy = (
                " (recompiles vLLM CUDA kernels from source, 60-90 minutes)"
                if service == "gateway"
                else ""
            )
            return {
                "error": (
                    f"rebuild is forbidden for '{service}'. "
                    f"Use manage(action='sync_restart', service='{service}') "
                    "to deploy code changes — that path is the cached/bind-mount "
                    "equivalent (~20s for mcp, instant for gateway via bind mount). "
                    f"A 'rebuild' here would do a full --no-cache build{heavy}, "
                    "which is ops-only via TUI: ./manage → Services → Build "
                    "Image, and only valid when the inference engine, pip "
                    "dependencies, or the Dockerfile itself change."
                )
            }

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
        if force and action in {"stop", "restart", "sync_restart"}:
            params["force"] = True

        # Long-running actions hold the connection open until done; extend socket timeout.
        sock_timeout = (
            timeout + _WAIT_HEALTHY_BUFFER
            if action in {"wait_healthy", "start", "restart", "sync_restart", "rebuild"}
            else _DEFAULT_TIMEOUT
        )

        raw = _call_manage(
            {"jsonrpc": "2.0", "method": action, "params": params, "id": 1},
            timeout=sock_timeout,
        )
        timed_out = bool(raw.get("_timeout"))
        result = _extract_result(raw)
        if (
            timed_out
            and action in {"start", "restart", "sync_restart", "rebuild"}
            and service
        ):
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
