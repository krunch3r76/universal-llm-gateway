"""Manage API — JSON-RPC 2.0 UDS server for agent-driven service lifecycle.

Listens on /tmp/universal-protocol/manage.sock. Dispatches start, stop,
rebuild, restart, status, health, and wait_healthy operations to
ServiceController. Emits manage.service.* observation events on every call.
Started unconditionally on ./manage launch alongside the Textual TUI.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from universal_event_bus import EventBus

from .api_dispatch import execute, write_json
from .manage_events import (
    ManageServiceCompleted,
    ManageServiceFailed,
    ManageServiceRequested,
)

if TYPE_CHECKING:
    from .controller.service_ctl.core import ServiceController

logger = logging.getLogger(__name__)

_SOCK_PATH = Path(
    os.environ.get("MANAGE_SOCKET", "/tmp/universal-protocol/manage.sock")
)
_MAX_REQUEST_BYTES = (
    65_536  # Max allowed size for an incoming JSON-RPC request in bytes
)
_LIVE_PROBE_TIMEOUT_S = 0.5
_TRACEBACK_TAIL_BYTES = 1500


class ManageSocketBusyError(RuntimeError):
    """Raised when manage.sock is already bound by a live process.

    Distinguished from generic OSError so the TUI can surface a
    targeted "another ./manage is already running" message instead
    of the generic permission/path failure path.
    """


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
        """Start the UDS listener, removing any stale socket from a prior run.

        Refuses to rebind when another live process is already listening on
        manage.sock. Silently unlink+rebind orphans the existing controller
        in-kernel: new connections route to the most-recently-bound inode but
        the older listener's fd is still held, leaving a "listening" socket
        only the orphaned process can read from. Detection is cooperative —
        the launcher (./manage) sees the refusal and exits cleanly.
        """
        _SOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _is_socket_alive(_SOCK_PATH):
            raise ManageSocketBusyError(
                f"manage.sock at {_SOCK_PATH} is already bound by a live "
                f"process. Another ./manage instance is running. Refusing to "
                f"rebind — would orphan the existing controller. Stop the "
                f"other instance (or kill its PID) before launching this one."
            )
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
                write_json(
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

            write_json(writer, resp)
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            # Normal client disconnect after one-shot JSON-RPC request (UDS close,
            # timeout, etc.); do not log as ERROR per quality gates. Events handle
            # the lifecycle telemetry.
            pass
        except Exception:
            logger.exception("Manage API connection error")
        finally:
            # Defensive: if the peer already tore down the socket, close()/
            # wait_closed() can raise. Letting that propagate leaves the
            # per-connection asyncio.Task in an errored state, which has been
            # observed to coincide with subsequent connections being reset
            # by peer until the controller is restarted.
            with contextlib.suppress(OSError, asyncio.CancelledError):
                writer.close()
                await writer.wait_closed()

    async def _dispatch(
        self, method: str, params: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Route method to ServiceController; emit before/after events."""
        service = params.get("service", "")
        t0 = time.monotonic()

        await self._event_bus.publish(
            ManageServiceRequested(method=method, service=service)
        )

        try:
            result = await execute(self._controller, method, service, params)
        except ValueError as exc:
            duration = round(time.monotonic() - t0, 3)
            await self._event_bus.publish(
                ManageServiceFailed(
                    method=method, service=service, error=str(exc), duration_s=duration
                )
            )
            return None, {"code": -32602, "message": str(exc)}
        except Exception as exc:
            duration = round(time.monotonic() - t0, 3)
            tb_tail = traceback.format_exc()[-_TRACEBACK_TAIL_BYTES:]
            error_msg = f"{type(exc).__name__}: {exc}"
            # logger.exception persists the full traceback to whatever handler
            # is wired (see app.py — the manage-api log handler). Surfacing the
            # tail in the JSON-RPC error.data lets agents read it without
            # round-tripping through the on-disk log.
            logger.exception("Manage API %s(%s) failed", method, service)
            await self._event_bus.publish(
                ManageServiceFailed(
                    method=method,
                    service=service,
                    error=error_msg,
                    duration_s=duration,
                )
            )
            return None, {
                "code": -32000,
                "message": error_msg,
                "data": {"traceback": tb_tail},
            }

        duration = round(time.monotonic() - t0, 3)
        await self._event_bus.publish(
            ManageServiceCompleted(method=method, service=service, duration_s=duration)
        )
        return result, None


def _is_socket_alive(path: Path) -> bool:
    """Return True iff a process is actively listening on path.

    Distinguishes a live listener from a stale socket file (which the existing
    unlink+rebind path correctly handles). A successful connect() means the
    kernel has a listener with a queued backlog accepting our SYN; only a live
    process can produce that. ConnectionRefusedError means the inode exists
    but no process owns it (stale file). FileNotFoundError means no inode.
    """
    if not path.exists():
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(_LIVE_PROBE_TIMEOUT_S)
    try:
        sock.connect(str(path))
    except (FileNotFoundError, ConnectionRefusedError):
        return False
    except OSError:
        # ENOTCONN, EAGAIN, ECONNRESET — treat as "not a live listener". The
        # caller will unlink and rebind; if a real listener does appear in
        # the race window, the bind itself will fail with EADDRINUSE.
        return False
    finally:
        sock.close()
    return True
