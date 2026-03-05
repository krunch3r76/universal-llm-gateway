"""RAG service lifecycle — start, stop, PID/socket management.

Part of the model_manager UI controller. Manages the RAG uvicorn process
with UDS or TCP transport, lock files, and stale socket cleanup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from ...model.service_state import ServiceState
from ..service_config import GATEWAY_DIR, read_rag_socket_path, read_rag_tcp_config
from .uvicorn_service import _start_uvicorn_service, _stop_uvicorn_service

_RAG_APP_MODULE = "services.rag.rag_service:app"
_RAG_LOCK_FILE = GATEWAY_DIR / "rag.lock"


async def start_rag(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Start RAG service as host process via uvicorn."""
    try:
        tcp_config = read_rag_tcp_config()
    except ValueError as e:
        return f"RAG config error: {e}"
    uds_mode = tcp_config is None
    socket_path = read_rag_socket_path() if uds_mode else None
    pid_file = GATEWAY_DIR / "rag.pid"

    return await _start_uvicorn_service(
        service_state=service_state,
        root=root,
        app_module=_RAG_APP_MODULE,
        pid_file=pid_file,
        lock_file=_RAG_LOCK_FILE,
        service_name="RAG service",
        socket_path=socket_path,
        tcp_config=tcp_config,
        log_dir=Path("/tmp/logs/universal-rag"),
        log_filename="rag.log",
    )


async def stop_rag(
    service_state: ServiceState,
    root: Path,
    kill_and_wait: Callable[..., Awaitable[str]],
) -> str:
    """Stop RAG service gracefully."""
    pid_file = GATEWAY_DIR / "rag.pid"
    try:
        tcp_config = read_rag_tcp_config()
    except ValueError as e:
        return f"RAG config error: {e}"
    uds_mode = tcp_config is None
    socket_path = read_rag_socket_path() if uds_mode else None

    return await _stop_uvicorn_service(
        service_state=service_state,
        kill_and_wait=kill_and_wait,
        pid_file=pid_file,
        lock_file=_RAG_LOCK_FILE,
        service_name="RAG",
        socket_path=socket_path,
        tcp_config=tcp_config,
        app_module=_RAG_APP_MODULE,
    )
