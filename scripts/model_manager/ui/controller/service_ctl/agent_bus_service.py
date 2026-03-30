"""Agent Bus service lifecycle — start, stop, PID/socket management.

Manages the agent-bus uvicorn process on UDS with lock files
and stale socket cleanup. Uses the shared _start/_stop_uvicorn_service
pattern (single socket, standard FastAPI app).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import (
    GATEWAY_DIR,
    ensure_agent_bus_config,
    load_agent_bus_config,
    load_mcp_config,
)
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_AGENT_BUS_APP_MODULE = "agent_bus_store.server:app"
_AGENT_BUS_PID_FILE = GATEWAY_DIR / "agent-bus.pid"
_AGENT_BUS_LOCK_FILE = GATEWAY_DIR / "agent-bus.lock"
_AGENT_BUS_SOCKET = Path(
    os.environ.get("AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock")
)
_AGENT_BUS_LOG_DIR = Path("/tmp/logs/agent-bus")


def _agent_bus_runtime_env() -> tuple[dict[str, str], str | None]:
    """Build runtime env for Agent Bus and validate DB path writability."""
    ensure_agent_bus_config()
    cfg = load_agent_bus_config()
    db_path = Path(cfg.db_path).expanduser()
    data_dir = db_path.parent
    data_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(data_dir, os.W_OK | os.X_OK):
        return {}, (
            f"Agent Bus data dir is not writable: {data_dir}. "
            f"Current uid/gid={os.getuid()}:{os.getgid()}."
        )
    if db_path.exists() and not os.access(db_path, os.W_OK):
        return {}, (
            f"Agent Bus database is not writable: {db_path}. "
            f"Current uid/gid={os.getuid()}:{os.getgid()}."
        )
    env = {
        "AGENT_BUS_DB_PATH": str(db_path),
        "AGENT_BUS_SOCK": str(_AGENT_BUS_SOCKET),
    }
    mcp_cfg = load_mcp_config()
    if mcp_cfg is not None and mcp_cfg.agent_bus_token:
        env["AGENT_BUS_TOKEN"] = mcp_cfg.agent_bus_token
    return env, None


async def start_agent_bus(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start agent-bus as host process via uvicorn on UDS."""
    extra_env, error = _agent_bus_runtime_env()
    if error is not None:
        return error
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_AGENT_BUS_APP_MODULE,
        pid_file=_AGENT_BUS_PID_FILE,
        lock_file=_AGENT_BUS_LOCK_FILE,
        service_name="Agent Bus",
        socket_path=_AGENT_BUS_SOCKET,
        tcp_config=None,
        log_dir=_AGENT_BUS_LOG_DIR,
        log_filename="agent-bus.log",
        extra_env=extra_env,
    )


async def stop_agent_bus(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop agent-bus gracefully."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_AGENT_BUS_PID_FILE,
        lock_file=_AGENT_BUS_LOCK_FILE,
        service_name="Agent Bus",
        socket_path=_AGENT_BUS_SOCKET,
        tcp_config=None,
        app_module=_AGENT_BUS_APP_MODULE,
    )
