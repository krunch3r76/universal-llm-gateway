"""Cortex API server — uvicorn on UDS with optional TCP."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from .main import create_app

logger = logging.getLogger("cortex-api")

_DEFAULT_SOCK = os.environ.get("CORTEX_API_SOCK", "/tmp/universal-protocol/cortex-api.sock")


async def run_service(
    *,
    db_path: str = "~/.cortex/cortex.db",
    sock: str = _DEFAULT_SOCK,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Main service lifecycle — parameterised for library use."""
    app = create_app(db_path=db_path)
    config = uvicorn.Config(
        app,
        uds=sock if host is None else None,
        host=host,
        port=port or 8200,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def start_cortex_api(
    *,
    db: str = "~/.cortex/cortex.db",
    sock: str = _DEFAULT_SOCK,
    host: str | None = None,
    port: int | None = None,
) -> asyncio.Task[None]:
    """Start cortex-api as a background asyncio task.

    Returns the task so callers can cancel/await it for shutdown.
    """
    db_path = os.path.expanduser(db)
    return asyncio.create_task(
        run_service(db_path=db_path, sock=sock, host=host, port=port)
    )
