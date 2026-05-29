"""FastAPI application factory for ``grokbuild-worker``.

Mirrors the cortex-api / agent-bus layout: ``create_app()`` returns the
FastAPI instance with all routes mounted under ``/api/v1/grokbuild/`` so the
worker-local URL == the Stargate-proxied URL (no path rewriting).

Lifespan startup performs the four checks documented in the phase plan
(sidecar dir, grok binary, auth dir, registry parent). Failures degrade
boot but never abort it — the operator may be inspecting the service.

Lifespan startup also installs the worker's UDS publisher into the lib's
event hook (``libs/grokbuild/events_core.register_uds_publisher``) so the
lib-level ``mcp.grokbuild.*`` event vocabulary reaches the event service
from this process. Without that hook the lib's ImportError fallback would
silently drop every audit-rich dispatch/create/remove/list event.

The version string surfaced through ``/health`` is the workspace git
HEAD short-SHA when reachable, else ``"unknown"`` (avoids embedding a
build-time stamp in the wheel).
"""

from __future__ import annotations

import subprocess
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from build_results import prune_spool
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from grokbuild.events_core import register_uds_publisher
from universal_logging import get_logger

from services.grokbuild_worker.config import WorkerConfig, load_config
from services.grokbuild_worker.events import (
    emit_degraded,
    emit_started,
    emit_stopped,
    publish_lib_signal,
)
from services.grokbuild_worker.routes.dispatches import router as dispatches_router
from services.grokbuild_worker.routes.health import _evaluate
from services.grokbuild_worker.routes.health import router as health_router
from services.grokbuild_worker.routes.models import router as models_router
from services.grokbuild_worker.routes.worktrees import router as worktrees_router
from services.grokbuild_worker.tracker import GrokbuildExecutionTracker

logger = get_logger(__name__)


def _resolve_version() -> str:
    """Return short git SHA of the workspace; ``"unknown"`` on failure."""
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
        logger.warning(
            "git rev-parse --short HEAD exited %d; using version='unknown'",
            proc.returncode,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "git rev-parse --short HEAD failed: %s; using version='unknown'", exc
        )
    return "unknown"


def _prepare_directories(cfg: WorkerConfig) -> list[str]:
    """Best-effort mkdir for sidecar + registry parent.

    Returns names of checks that *couldn't* be repaired (still failing).
    """
    failures: list[str] = []
    try:
        cfg.sidecar_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create sidecar dir %s: %s", cfg.sidecar_dir, exc)
        failures.append("sidecar_dir")
    try:
        cfg.registry_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "Could not create registry parent %s: %s", cfg.registry_path.parent, exc
        )
        failures.append("registry")
    return failures


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: prep dirs + run health checks; shutdown: emit stopped event."""
    # Install the UDS publisher hook BEFORE anything that might emit a lib
    # event (e.g. registry recovery on grokbuild.registry import paths).
    register_uds_publisher(publish_lib_signal)

    cfg: WorkerConfig = load_config()
    app.state.worker_config = cfg
    app.state.worker_version = _resolve_version()
    app.state.worker_started_at = time.monotonic()

    _prepare_directories(cfg)
    checks = _evaluate(cfg)
    degraded = [name for name, status in checks.items() if status != "ok"]
    if degraded:
        logger.warning("grokbuild-worker booting degraded: %s", degraded)
        try:
            emit_degraded(degraded)
        except Exception:  # noqa: BLE001 — event bus failures must not block boot
            logger.exception("Failed to emit grokbuild.worker.degraded")

    try:
        emit_started(
            version=app.state.worker_version,
            deploy_shape=cfg.deploy_shape,
            port=cfg.port,
            degraded_checks=degraded,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to emit grokbuild.worker.started")

    tracker = GrokbuildExecutionTracker()
    app.state.grokbuild_tracker = tracker
    try:
        purged = await tracker.cleanup_orphans()
        logger.info("grokbuild tracker cleanup_orphans purged %d entries", purged)
    except Exception:  # noqa: BLE001 — orphan cleanup must not block boot
        logger.exception("Failed to run grokbuild tracker cleanup_orphans")

    try:
        pruned = prune_spool()
        logger.info("build_results prune_spool removed %d aged spool dirs", pruned)
    except Exception:  # noqa: BLE001 — spool prune must not block boot
        logger.exception("Failed to run build_results prune_spool")

    logger.info(
        "grokbuild-worker started: version=%s deploy_shape=%s port=%d degraded=%s",
        app.state.worker_version,
        cfg.deploy_shape,
        cfg.port,
        degraded,
    )

    try:
        yield
    finally:
        try:
            await tracker.drain(timeout_seconds=30.0)
        except Exception:  # noqa: BLE001 — drain failures must not mask shutdown
            logger.exception("Failed to drain grokbuild tracker on shutdown")
        uptime = time.monotonic() - app.state.worker_started_at
        try:
            emit_stopped(reason="lifespan_exit", uptime_s=uptime)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit grokbuild.worker.stopped")
        logger.info("grokbuild-worker stopped after %.1fs", uptime)


def create_app() -> FastAPI:
    """Construct the FastAPI app with all routers mounted."""
    app = FastAPI(
        title="grokbuild-worker",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/v1/grokbuild/docs",
        openapi_url="/api/v1/grokbuild/openapi.json",
    )

    @app.exception_handler(Exception)
    async def _log_unhandled(request: Request, exc: Exception) -> JSONResponse:
        tb = traceback.format_exc()
        logger.error(
            "UNHANDLED %s %s: %s\n%s", request.method, request.url.path, exc, tb
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "error": str(exc),
                "path": str(request.url.path),
            },
        )

    app.include_router(health_router)
    app.include_router(models_router)
    app.include_router(worktrees_router)
    app.include_router(dispatches_router)
    return app


app = create_app()
