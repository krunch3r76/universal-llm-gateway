"""Local Edge runtime recovery helpers.

Keeps recovery policy close to the LocalEdgeClient without embedding Docker
or compose logic inside Stargate. Recovery requests go through the in-repo
manage API over UDS.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from transport_utils import MANAGE_SOCKET
from universal_event_bus.events.debug import emit_debug_event
from universal_logging import get_logger

logger = get_logger(__name__)

_MANAGE_SOCK = MANAGE_SOCKET
_RESTORE_COOLDOWN_S = 30.0
_START_TIMEOUT_S = 30.0
_WAIT_HEALTHY_TIMEOUT_S = 45.0
_WAIT_HEALTHY_BUFFER_S = 15.0
_MESSAGE_LIMIT = 240
_RESTORABLE_ERRORS = (FileNotFoundError, ConnectionRefusedError)
_MAX_REPO_WALK = 8
_MANAGE_SENTINEL = "manage"
_MANAGE_SOCK_NOT_FOUND = "manage.sock not found"


def _trim_message(message: Any) -> str:
    text = str(message).strip()
    if len(text) <= _MESSAGE_LIMIT:
        return text
    return f"{text[:_MESSAGE_LIMIT]}..."


def _node_id_from_peer_id(peer_id: str) -> str:
    return (
        peer_id.removeprefix("edge-")
        .removeprefix("relay-")
        .removeprefix("master-")
        .removesuffix("-gateway")
    )


def _resolve_repo_root() -> Path:
    env = os.environ.get("ULG_REPO_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    current = Path(__file__).resolve().parent
    for _ in range(_MAX_REPO_WALK):
        if (current / _MANAGE_SENTINEL).is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise RuntimeError(
        f"Could not locate repo root (manage sentinel) from {__file__}. "
        "Set ULG_REPO_ROOT to override."
    )


def _read_desired_state(node_id: str) -> str:
    repo_root = _resolve_repo_root()
    repo_str = str(repo_root)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)
    from scripts.model_manager import relay_state

    return relay_state.read_desired_state(node_id)


def _resolve_event_bus() -> Any | None:
    try:
        from systems.federation.integration.lifecycle import get_federation_integration

        integration = get_federation_integration()
        return getattr(integration, "_event_bus", None)
    except Exception:
        return None


async def _call_manage(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(_MANAGE_SOCK),
            timeout=timeout,
        )
        writer.write(json.dumps(body).encode() + b"\n")
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        response = json.loads(raw.decode().strip()) if raw else {}
    except FileNotFoundError:
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
        logger.warning("Local edge recovery manage call failed: %s", exc, exc_info=True)
        return {"error": f"Manage API call failed: {exc}"}
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    error = response.get("error")
    if error:
        if isinstance(error, dict):
            return {"error": error.get("message", str(error))}
        return {"error": str(error)}
    result = response.get("result", response)
    return result if isinstance(result, dict) else {"result": result}


class LocalEdgeRecoveryCoordinator:
    """Throttle and execute concrete recovery for a vanished local edge."""

    def __init__(
        self,
        *,
        relay_stargate_id: str,
        peer_id: str,
        socket_path: str,
    ) -> None:
        self._relay_stargate_id = relay_stargate_id
        self._peer_id = peer_id
        self._socket_path = socket_path
        self._node_id = _node_id_from_peer_id(peer_id)
        self._restore_task: asyncio.Task[None] | None = None
        self._last_restore_started_at = 0.0
        self._outage_signaled = False

    async def note_connected(self) -> None:
        """Reset outage state when the edge session comes back."""
        self._last_restore_started_at = 0.0
        self._outage_signaled = False
        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()
            try:
                await self._restore_task
            except asyncio.CancelledError:
                pass
        self._restore_task = None
        await emit_debug_event(
            "debug.federation.reconnect",
            {
                "step": "connected",
                "relay_stargate_id": self._relay_stargate_id,
                "peer_id": self._peer_id,
                "socket_path": self._socket_path,
            },
            source="stargate",
        )

    async def shutdown(self) -> None:
        """Cancel any outstanding restore task during client shutdown."""
        if self._restore_task and not self._restore_task.done():
            self._restore_task.cancel()
            try:
                await self._restore_task
            except asyncio.CancelledError:
                pass
        self._restore_task = None

    async def _emit_managed_down_skip(self, exc: Exception) -> None:
        await emit_debug_event(
            "debug.federation.restore",
            {
                "step": "skipped",
                "reason": "managed_down",
                "relay_stargate_id": self._relay_stargate_id,
                "peer_id": self._peer_id,
                "socket_path": self._socket_path,
                "error_type": type(exc).__name__,
            },
            source="stargate",
        )

    async def _emit_edge_container_exited(self, exc: Exception) -> None:
        event_bus = _resolve_event_bus()
        if event_bus is None:
            return
        from src.scheduling.events.federation_signaling import (
            FederationEdgeContainerExited,
        )

        try:
            await event_bus.publish_nowait(
                FederationEdgeContainerExited(
                    node_id=self._node_id,
                    relay_stargate_id=self._relay_stargate_id,
                    error_type=type(exc).__name__,
                    socket_path=self._socket_path,
                )
            )
        except Exception as emit_exc:
            logger.warning(
                "federation.edge.container.exited publish failed: %s",
                emit_exc,
                exc_info=emit_exc,
            )

    async def handle_connect_failure(self, exc: Exception) -> None:
        """Request edge restoration for runtime-level UDS failures."""
        if not isinstance(exc, _RESTORABLE_ERRORS):
            return

        if self._restore_task and not self._restore_task.done():
            await emit_debug_event(
                "debug.federation.restore",
                {
                    "step": "skipped",
                    "reason": "inflight",
                    "relay_stargate_id": self._relay_stargate_id,
                    "peer_id": self._peer_id,
                    "socket_path": self._socket_path,
                    "error_type": type(exc).__name__,
                },
                source="stargate",
            )
            return

        now = asyncio.get_running_loop().time()
        cooldown_remaining_s = max(
            0.0,
            _RESTORE_COOLDOWN_S - (now - self._last_restore_started_at),
        )
        if self._last_restore_started_at and cooldown_remaining_s > 0.0:
            await emit_debug_event(
                "debug.federation.restore",
                {
                    "step": "skipped",
                    "reason": "cooldown",
                    "cooldown_remaining_s": round(cooldown_remaining_s, 3),
                    "relay_stargate_id": self._relay_stargate_id,
                    "peer_id": self._peer_id,
                    "socket_path": self._socket_path,
                    "error_type": type(exc).__name__,
                },
                source="stargate",
            )
            return

        if _read_desired_state(self._node_id) == "managed_down":
            await self._emit_managed_down_skip(exc)
            return

        if not self._outage_signaled:
            await self._emit_edge_container_exited(exc)
            self._outage_signaled = True

        self._last_restore_started_at = now
        self._restore_task = asyncio.create_task(
            self._run_restore(trigger_error=exc),
            name=f"local-edge-restore-{self._peer_id}",
        )

    async def _fallback_edge_only(self) -> dict[str, Any]:
        repo_root = _resolve_repo_root()
        manage_script = repo_root / _MANAGE_SENTINEL
        proc = await asyncio.create_subprocess_exec(
            str(manage_script),
            "relay",
            "--edge-only",
            "--node-id",
            self._node_id,
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = (stderr or stdout).decode().strip()
            return {
                "error": _trim_message(
                    detail or f"relay --edge-only exited {proc.returncode}"
                )
            }
        return {"message": _trim_message(stdout.decode())}

    async def _run_restore(self, *, trigger_error: Exception) -> None:
        if _read_desired_state(self._node_id) == "managed_down":
            await self._emit_managed_down_skip(trigger_error)
            return

        error_type = type(trigger_error).__name__
        error_detail = _trim_message(str(trigger_error) or self._socket_path)
        await emit_debug_event(
            "debug.federation.restore",
            {
                "step": "requested",
                "action": "start",
                "service": "gateway",
                "relay_stargate_id": self._relay_stargate_id,
                "peer_id": self._peer_id,
                "socket_path": self._socket_path,
                "trigger_error_type": error_type,
                "trigger_error": error_detail,
            },
            source="stargate",
        )

        start_result = await _call_manage(
            "start",
            {"service": "gateway"},
            timeout=_START_TIMEOUT_S,
        )
        if "error" in start_result:
            error_message = start_result["error"]
            if _MANAGE_SOCK_NOT_FOUND in error_message:
                await emit_debug_event(
                    "debug.federation.restore",
                    {
                        "step": "requested",
                        "action": "relay --edge-only",
                        "relay_stargate_id": self._relay_stargate_id,
                        "peer_id": self._peer_id,
                        "socket_path": self._socket_path,
                    },
                    source="stargate",
                )
                try:
                    start_result = await self._fallback_edge_only()
                except Exception as exc:
                    await emit_debug_event(
                        "debug.federation.restore",
                        {
                            "step": "failed",
                            "stage": "relay --edge-only",
                            "relay_stargate_id": self._relay_stargate_id,
                            "peer_id": self._peer_id,
                            "socket_path": self._socket_path,
                            "error": _trim_message(exc),
                        },
                        source="stargate",
                    )
                    return
            if "error" in start_result:
                await emit_debug_event(
                    "debug.federation.restore",
                    {
                        "step": "failed",
                        "stage": "start",
                        "relay_stargate_id": self._relay_stargate_id,
                        "peer_id": self._peer_id,
                        "socket_path": self._socket_path,
                        "error": _trim_message(start_result["error"]),
                    },
                    source="stargate",
                )
                return

        await emit_debug_event(
            "debug.federation.restore",
            {
                "step": "started",
                "action": "start",
                "service": "gateway",
                "relay_stargate_id": self._relay_stargate_id,
                "peer_id": self._peer_id,
                "socket_path": self._socket_path,
                "detail": _trim_message(start_result.get("message", "")),
            },
            source="stargate",
        )

        wait_result = await _call_manage(
            "wait_healthy",
            {"service": "gateway", "timeout": _WAIT_HEALTHY_TIMEOUT_S},
            timeout=_WAIT_HEALTHY_TIMEOUT_S + _WAIT_HEALTHY_BUFFER_S,
        )
        if "error" in wait_result:
            await emit_debug_event(
                "debug.federation.restore",
                {
                    "step": "failed",
                    "stage": "wait_healthy",
                    "relay_stargate_id": self._relay_stargate_id,
                    "peer_id": self._peer_id,
                    "socket_path": self._socket_path,
                    "error": _trim_message(wait_result["error"]),
                },
                source="stargate",
            )
            return

        await emit_debug_event(
            "debug.federation.restore",
            {
                "step": "healthy",
                "action": "wait_healthy",
                "service": "gateway",
                "relay_stargate_id": self._relay_stargate_id,
                "peer_id": self._peer_id,
                "socket_path": self._socket_path,
                "waited_s": wait_result.get("waited_s"),
            },
            source="stargate",
        )
