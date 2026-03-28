"""Agent bus server - FastAPI on UDS with optional TCP."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from .db import init_db
from .routes.messages import router as messages_router
from .routes.threads import router as threads_router
from .routes.turns import router as turns_router

logger = logging.getLogger("agent-bus")


def create_app(*, db_path: str | None = None) -> FastAPI:
    """Build the FastAPI application.

    If db_path is provided, it overrides AGENT_BUS_DB_PATH.
    """
    if db_path is not None:
        os.environ["AGENT_BUS_DB_PATH"] = db_path

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        logger.info("agent-bus started")
        yield

    app = FastAPI(
        title="agent-bus",
        version="2.0.0",
        description="Inter-agent message bus for Web Claude, API Claude, and Cursor Claude.",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(messages_router)
    app.include_router(turns_router)
    app.include_router(threads_router)
    return app


app = create_app()


async def run_service(
    *,
    db_path: str = "/data/messages.db",
    sock: str = "/tmp/universal-protocol/agent-bus.sock",
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Main service lifecycle - parameterized for library use."""
    app = create_app(db_path=db_path)
    config = uvicorn.Config(
        app,
        uds=sock if host is None else None,
        host=host,
        port=port or 8100,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def start_agent_bus(
    *,
    db: str = "~/.agent-bus/messages.db",
    sock: str = "/tmp/universal-protocol/agent-bus.sock",
    host: str | None = None,
    port: int | None = None,
) -> asyncio.Task[None]:
    """Start the agent bus as a background asyncio task.

    Returns the task so callers can cancel/await it for shutdown.
    """
    db_path = os.path.expanduser(db)
    return asyncio.create_task(
        run_service(db_path=db_path, sock=sock, host=host, port=port)
    )
