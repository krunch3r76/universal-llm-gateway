"""Cortex API server — uvicorn on UDS with optional TCP."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn
from universal_logging import get_logger

from .main import create_app

logger = get_logger("cortex-api")


def _default_sock() -> str:
    """Resolve the default cortex-api UDS path from env with /tmp fallback."""
    return os.environ.get("CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock")


def _resolve_sock(sock: str | None) -> str:
    """Normalize the configured socket path for direct library starts."""
    raw = sock or _default_sock()
    path = Path(os.path.expanduser(raw))
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


async def run_service(
    *,
    db_path: str = "~/.cortex/cortex.db",
    sock: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Main service lifecycle — parameterised for library use."""
    app = create_app(db_path=db_path)
    resolved_sock = _resolve_sock(sock)
    config = uvicorn.Config(
        app,
        uds=resolved_sock if host is None else None,
        host=host,
        port=port or 8200,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def start_cortex_api(
    *,
    db: str = "~/.cortex/cortex.db",
    sock: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> asyncio.Task[None]:
    """Start cortex-api as a background asyncio task.

    Returns the task so callers can cancel/await it for shutdown.
    """
    db_path = os.path.expanduser(db)
    return asyncio.create_task(
        run_service(db_path=db_path, sock=_resolve_sock(sock), host=host, port=port)
    )
