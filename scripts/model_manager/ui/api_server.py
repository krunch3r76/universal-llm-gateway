"""Manage API — JSON-RPC 2.0 UDS server for agent-driven service lifecycle.

Listens on /tmp/universal-protocol/manage.sock. Dispatches start, stop,
rebuild, restart, status, health, and wait_healthy operations to
ServiceController. Emits manage.service.* observation events on every call.
Started unconditionally on ./manage launch alongside the Textual TUI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_event_bus import EventBus

from .manage_events import (
    ManageServiceCompleted,
    ManageServiceFailed,
    ManageServiceRequested,
)

if TYPE_CHECKING:
    from .controller.service_ctl.core import ServiceController
    from .model.service_state import ServiceInfo, ServiceState

logger = logging.getLogger(__name__)

_SOCK_PATH = Path(
    os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")
)
_MAX_REQUEST_BYTES = (
    65_536  # Max allowed size for an incoming JSON-RPC request in bytes
)
_VALID_SERVICES = frozenset(
    {
        "gateway",
        "stargate",
        "rag",
        "cloud_proxy",
        "mcp",
        "event_service",
        "cortex_api",
        "agent_bus",
    }
)  # All recognized service names for API operations
# Services that support the 'rebuild' operation (container services with local Dockerfiles).
_REBUILD_SERVICES = frozenset(
    {"gateway", "mcp", "event_service", "cortex_api", "agent_bus"}
)


class ManageAPIServer:
    """JSON-RPC 2.0 UDS server exposing ServiceController lifecycle operations.

    One asyncio task per client connection; no locks. All synchronous
    ServiceState calls run in asyncio.to_thread to avoid blocking the
    Textual event loop. Lifecycle events are emitted via the shared EventBus.
    """

    def __init__(self, controller: ServiceController, event_bus: EventBus) -> None:
        self._controller = controller
        self._event_bus = event_bus
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        """Start the UDS listener, removing any stale socket from a prior run."""
        _SOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SOCK_PATH.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_connection, path=str(_SOCK_PATH)
        )
        logger.info("Manage API server listening on %s", _SOCK_PATH)

    async def stop(self) -> None:
        """Close the UDS listener and remove the socket file."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        _SOCK_PATH.unlink(missing_ok=True)
        logger.info("Manage API server stopped")

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read one JSON-RPC request, write one response, then close."""
        try:
            raw = await reader.readline()
            if not raw:
                return

            def _write_error(code: int, message: str, req_id: Any = None) -> None:
                _write_json(
                    writer,
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": code, "message": message},
                        "id": req_id,
                    },
                )

            if len(raw) > _MAX_REQUEST_BYTES:
                _write_error(-32600, "Request too large")
                await writer.drain()
                return
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                _write_error(-32700, "Parse error")
                await writer.drain()
                return

            req_id = req.get("id")
            method = req.get("method", "")
            params: dict[str, Any] = req.get("params") or {}

            result, err = await self._dispatch(method, params)

            if err:
                resp: dict[str, Any] = {"jsonrpc": "2.0", "error": err, "id": req_id}
            else:
                resp = {"jsonrpc": "2.0", "result": result, "id": req_id}

            _write_json(writer, resp)
            await writer.drain()
        except Exception:
            logger.exception("Manage API connection error")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _dispatch(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Route method to ServiceController; emit before/after events."""
        service = params.get("service", "")
        t0 = time.monotonic()

        await self._event_bus.publish_async(
            ManageServiceRequested(method=method, service=service)
        )

        try:
            result = await _execute(self._controller, method, service, params)
        except ValueError as exc:
            duration = round(time.monotonic() - t0, 3)
            await self._event_bus.publish_async(
                ManageServiceFailed(
                    method=method, service=service, error=str(exc), duration_s=duration
                )
            )
            return None, {"code": -32602, "message": str(exc)}
        except Exception as exc:
            duration = round(time.monotonic() - t0, 3)
            logger.error("Manage API %s(%s) failed: %s", method, service, exc)
            await self._event_bus.publish_async(
                ManageServiceFailed(
                    method=method, service=service, error=str(exc), duration_s=duration
                )
            )
            return None, {"code": -32000, "message": str(exc)}

        duration = round(time.monotonic() - t0, 3)
        await self._event_bus.publish_async(
            ManageServiceCompleted(method=method, service=service, duration_s=duration)
        )
        return result, None


# ---------------------------------------------------------------------------
# Dispatch helpers — module-level for testability
# ---------------------------------------------------------------------------


async def _execute(
    ctl: ServiceController, method: str, service: str, params: dict[str, Any]
) -> dict[str, Any]:
    """Dispatch a validated JSON-RPC method to ServiceController."""
    svc = ctl.service_state

    match method:
        case "status":
            infos = await asyncio.to_thread(svc.check_all)
            return {
                "services": {
                    i.name.lower().replace(" ", "_"): i.status.value for i in infos
                },
                "build": await asyncio.to_thread(_gateway_build_status, ctl),
            }

        case "health":
            _require_service(service)
            info = await asyncio.to_thread(_check_one, svc, service)
            result = {
                "service": service,
                "status": info.status.value,
                "detail": info.detail,
            }
            if service == "gateway":
                result["build"] = await asyncio.to_thread(_gateway_build_status, ctl)
            return result

        case "wait_healthy":
            _require_service(service)
            timeout = float(params.get("timeout", 120.0))
            waited = await _wait_healthy(svc, service, timeout)
            return {"healthy": True, "waited_s": round(waited, 1)}

        case "start":
            _require_service(service)
            msg = await _start(ctl, service)
            return {"status": "ok", "message": msg}

        case "stop":
            _require_service(service)
            msg = await _stop(ctl, service)
            return {"status": "ok", "message": msg}

        case "restart":
            _require_service(service)
            stop_msg = await _stop(ctl, service)
            start_msg = await _start(ctl, service)
            return {"status": "ok", "message": f"{stop_msg}\n{start_msg}"}

        case "rebuild":
            _require_service(service)
            if service not in _REBUILD_SERVICES:
                raise ValueError(
                    f"rebuild not supported for '{service}'; "
                    f"supported: {', '.join(sorted(_REBUILD_SERVICES))}"
                )
            msg = await _rebuild(ctl, service)
            return {"status": "ok", "message": msg}

        case _:
            raise ValueError(
                f"Unknown method: '{method}'. "
                "Valid: status, health, wait_healthy, start, stop, restart, rebuild"
            )


def _require_service(service: str) -> None:
    """Raise ValueError if service is not a known service name."""
    if service not in _VALID_SERVICES:
        raise ValueError(
            f"Unknown service: '{service}'. Valid: {', '.join(sorted(_VALID_SERVICES))}"
        )


def _check_one(svc: ServiceState, service: str) -> ServiceInfo:
    """Return ServiceInfo for a single service (synchronous, call via to_thread)."""
    if service not in _VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    try:
        return getattr(svc, f"check_{service}")()
    except AttributeError:
        raise ValueError(f"Unknown service: '{service}'")


def _gateway_build_status(ctl: ServiceController) -> dict[str, Any]:
    """Return gateway build state for status/progress probes."""
    info = ctl.check_image()
    return {
        "running": ctl.build_running,
        "image_status": info.status.value,
        "image_id": info.image_id,
        "created": info.created,
        "size": info.size,
    }


async def _wait_healthy(svc: ServiceState, service: str, timeout: float) -> float:
    """Poll service health until RUNNING or timeout; return elapsed seconds."""
    from .model.service_state import ServiceStatus

    t0 = time.monotonic()
    deadline = t0 + timeout
    while True:
        info = await asyncio.to_thread(_check_one, svc, service)
        if info.status == ServiceStatus.RUNNING:
            return time.monotonic() - t0
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"'{service}' not healthy after {timeout:.0f}s "
                f"(last status: {info.status.value})"
            )
        await asyncio.sleep(2.0)


async def _start(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController start method."""
    if service not in _VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    try:
        return await getattr(ctl, f"start_{service}")()
    except AttributeError:
        raise ValueError(f"Unknown service: '{service}'")


async def _stop(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController stop method."""
    if service not in _VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    try:
        return await getattr(ctl, f"stop_{service}")()
    except AttributeError:
        raise ValueError(f"Unknown service: '{service}'")


async def _rebuild(ctl: ServiceController, service: str) -> str:
    """Rebuild and restart a container service; drain async generator for gateway.

    Build failure detection is line-based (last line contains FAILED/build failed).
    Prefer subprocess return code when ServiceController exposes it.
    """
    if service == "gateway":
        if ctl.build_running:
            raise RuntimeError("Gateway build already in progress")
        lines: list[str] = []
        async for line in ctl.build_image(no_cache=True):
            lines.append(line)
        last = lines[-1] if lines else "Build completed."
        if "FAILED" in last or last.lower().startswith("build failed"):
            raise RuntimeError(last)
        start_msg = await ctl.start_gateway()
        return f"{last}\n{start_msg}"
    if service == "mcp":
        return await ctl.rebuild_mcp()
    if service == "event_service":
        return await ctl.rebuild_event_service()
    if service == "cortex_api":
        return await ctl.rebuild_cortex_api()
    if service == "agent_bus":
        return await ctl.rebuild_agent_bus()
    raise ValueError(f"rebuild not supported for '{service}'")


def _write_json(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Serialize obj to JSON and write a newline-terminated line to writer."""
    writer.write(json.dumps(obj).encode() + b"\n")
