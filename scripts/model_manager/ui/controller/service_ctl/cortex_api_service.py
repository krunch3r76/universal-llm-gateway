"""Cortex API service lifecycle — start, stop, PID/socket management.

Manages the cortex-api uvicorn process on UDS with lock files
and stale socket cleanup. Uses the shared _start/_stop_uvicorn_service
pattern (single socket, standard FastAPI app).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_CORTEX_APP_MODULE = "cortex_store.main:app"
_CORTEX_PID_FILE = GATEWAY_DIR / "cortex-api.pid"
_CORTEX_LOCK_FILE = GATEWAY_DIR / "cortex-api.lock"
_CORTEX_SOCKET = Path("/tmp/universal-protocol/cortex-api.sock")
_CORTEX_LOG_DIR = Path("/tmp/logs/cortex-api")


async def start_cortex_api(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],  # noqa: ARG001
) -> str:
    """Start cortex-api as host process via uvicorn on UDS."""
    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_CORTEX_APP_MODULE,
        pid_file=_CORTEX_PID_FILE,
        lock_file=_CORTEX_LOCK_FILE,
        service_name="Cortex API",
        socket_path=_CORTEX_SOCKET,
        tcp_config=None,
        log_dir=_CORTEX_LOG_DIR,
        log_filename="cortex-api.log",
    )


async def stop_cortex_api(
    service_state: ServiceState,
    root: Path,  # noqa: ARG001
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop cortex-api gracefully."""
    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=_CORTEX_PID_FILE,
        lock_file=_CORTEX_LOCK_FILE,
        service_name="Cortex API",
        socket_path=_CORTEX_SOCKET,
        tcp_config=None,
        app_module=_CORTEX_APP_MODULE,
    )
