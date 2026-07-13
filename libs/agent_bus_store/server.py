"""Agent bus server - FastAPI on UDS with optional TCP."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .db import init_db
from .reconcile import reconcile_orphaned_dispatches
from .routes.messages import router as messages_router
from .routes.threads import router as threads_router
from .routes.turns import router as turns_router
from .routes.wait import router as wait_router
from .turns_models import MAX_TURN_BODY_CHARS, body_too_large_envelope
from .watchdog import run_watchdog

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
        try:
            reaped = reconcile_orphaned_dispatches()
            if reaped:
                logger.warning("orphan reconcile: reaped %s dispatch link(s)", reaped)
        except Exception:
            logger.exception("orphan reconcile failed at startup")
        watchdog_task = asyncio.create_task(run_watchdog())
        logger.info("agent-bus started")
        try:
            yield
        finally:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task

    app = FastAPI(
        title="agent-bus",
        version="2.0.0",
        description="Inter-agent message bus for Web Claude, API Claude, and Cursor Claude.",
        lifespan=lifespan,
    )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Promote the body-length-too-long failure to a structured 413 so any
        # caller (MCP relay, scripts, future handlers) gets the same envelope.
        # All other validation failures fall through to the FastAPI 422 default.
        for err in exc.errors():
            if err.get("type") != "string_too_long":
                continue
            loc = err.get("loc") or ()
            if not (len(loc) >= 2 and loc[0] == "body" and loc[-1] == "body"):
                continue
            ctx = err.get("ctx") or {}
            limit = int(ctx.get("max_length", MAX_TURN_BODY_CHARS))
            input_value = err.get("input")
            body_chars = len(input_value) if isinstance(input_value, str) else 0
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "detail": body_too_large_envelope(
                        limit=limit, body_chars=body_chars
                    )
                },
            )
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": jsonable_encoder(exc.errors())},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(messages_router)
    app.include_router(turns_router)
    app.include_router(threads_router)
    app.include_router(wait_router)
    return app


app = create_app()


async def run_service(
    *,
    db_path: str = "/data/messages.db",
    sock: str = os.environ.get(
        "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
    ),
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
    sock: str = os.environ.get(
        "AGENT_BUS_SOCK", "/tmp/universal-protocol/agent-bus.sock"
    ),
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
