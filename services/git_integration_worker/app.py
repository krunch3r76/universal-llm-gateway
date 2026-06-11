"""FastAPI application factory for ``git-integration-worker``."""

from __future__ import annotations

import subprocess
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from git_integrate.events import register_uds_publisher
from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.routes.cursor_sdk import (
    router as cursor_sdk_router,
)
from services.git_integration_worker.routes.health import router as health_router
from services.git_integration_worker.routes.integrate import router as integrate_router

logger = get_logger(__name__)


def _resolve_version() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[2],
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git rev-parse failed: %s", exc)
    return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    register_uds_publisher(publish_lib_signal)
    cfg: WorkerConfig = load_config()
    app.state.worker_config = cfg
    app.state.worker_version = _resolve_version()
    app.state.worker_started_at = time.monotonic()
    logger.info(
        "git-integration-worker started: version=%s port=%d source_repo=%s",
        app.state.worker_version,
        cfg.port,
        cfg.source_repo,
    )
    try:
        yield
    finally:
        logger.info(
            "git-integration-worker stopped after %.1fs",
            time.monotonic() - app.state.worker_started_at,
        )


def create_app() -> FastAPI:
    app = FastAPI(
        title="git-integration-worker",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/v1/git/docs",
        openapi_url="/api/v1/git/openapi.json",
    )

    @app.exception_handler(Exception)
    async def _log_unhandled(request: Request, exc: Exception) -> JSONResponse:
        tb = traceback.format_exc()
        logger.error(
            "UNHANDLED %s %s: %s\n%s", request.method, request.url.path, exc, tb
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal Server Error", "error": str(exc)},
        )

    app.include_router(health_router)
    app.include_router(integrate_router)
    app.include_router(cursor_sdk_router)
    return app


app = create_app()
