"""FastAPI application factory for ``git-integration-worker``."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from git_integrate.events import register_uds_publisher
from universal_logging import get_logger

from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.background_supervisor import supervise
from services.git_integration_worker.config import WorkerConfig, load_config
from services.git_integration_worker.cursor_auto.auto_worker_loop import (
    auto_worker_loop,
    orphan_scanner_loop,
)
from services.git_integration_worker.cursor_auto.closeout_outbox import (
    CloseoutOutboxStore,
)
from services.git_integration_worker.cursor_auto.execute_runner import (
    clear_tool_op_invoker,
)
from services.git_integration_worker.cursor_auto.execute_tool_op_invoker import (
    register_production_invoker,
)
from services.git_integration_worker.cursor_auto.hop_cadence import hop_cadence_loop
from services.git_integration_worker.cursor_auto.job_reconcile import (
    shutdown_auto_jobs,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)
from services.git_integration_worker.cursor_sdk_events import (
    register_cursor_sdk_event_publisher,
)
from services.git_integration_worker.cursor_sdk_orphan import shutdown_active_bridges
from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.git_worker_drain_events import (
    register_git_worker_drain_event_publisher,
)
from services.git_integration_worker.git_worker_lifecycle_events import (
    emit_git_worker_started,
    register_git_worker_lifecycle_event_publisher,
)
from services.git_integration_worker.lane_b_sweeper import lane_b_sweeper_loop
from services.git_integration_worker.routes.admin import router as admin_router
from services.git_integration_worker.routes.cursor_auto import (
    router as cursor_auto_router,
)
from services.git_integration_worker.routes.cursor_catalog import (
    router as cursor_catalog_router,
)
from services.git_integration_worker.routes.cursor_sdk import (
    router as cursor_sdk_router,
)
from services.git_integration_worker.routes.cursor_sdk import (
    stale_lease_sweeper,
)
from services.git_integration_worker.routes.health import router as health_router
from services.git_integration_worker.routes.integrate import router as integrate_router
from services.git_integration_worker.routes.triggers import router as triggers_router
from services.git_integration_worker.startup_persistence import (
    schedule_startup_persistence,
)
from services.git_integration_worker.trigger_service.loop import trigger_fire_loop
from services.git_integration_worker.ulg_story_projector import ulg_story_projector_loop

_DRAIN_LIFESPAN_TIMEOUT_S = float(os.getenv("GIT_WORKER_DRAIN_LIFESPAN_TIMEOUT", "30"))

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
    register_cursor_sdk_event_publisher(publish_lib_signal)
    register_git_worker_drain_event_publisher(publish_lib_signal)
    register_git_worker_lifecycle_event_publisher(publish_lib_signal)
    # Construct the dispatch ledger at startup, under the real worker HOME and
    # before any per-dispatch HOME swap, so the cursor_sdk_dispatches table
    # exists regardless of which dispatch op touches the ledger first.
    ledger = CursorDispatchLedger.instance()
    CloseoutOutboxStore.instance()
    # Worker generation identity: fresh uuid + wall-clock boot ts per process.
    # Drain events carry these so a Phase-2 manage supervisor can detect a
    # stale-epoch event emitted by a prior worker generation across a restart.
    app.state.worker_id = str(uuid4())
    worker_boot_ts = datetime.now(UTC).isoformat()
    app.state.worker_boot_ts = worker_boot_ts
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id=app.state.worker_id,
        pid=os.getpid(),
        worker_started_at=worker_boot_ts,
    )
    app.state.admission_controller = controller
    cfg: WorkerConfig = load_config()
    app.state.worker_config = cfg
    app.state.worker_version = _resolve_version()
    app.state.worker_started_at = time.monotonic()
    # Bind-first: persistence reconcile/replay must not hold the health port
    # off the wire (2026-08-03 boot hang — unbounded pre-yield work).
    schedule_startup_persistence(app)
    register_production_invoker()
    app.state.shutting_down = False
    supervise(app, "stale_lease_sweeper", lambda: stale_lease_sweeper(app))
    # Respawn the lane loop: its absence deregisters the cursor-auto handler and
    # parks every agent_bus.request, including the propagate repair path.
    supervise(app, "cursor_auto_worker", lambda: auto_worker_loop(app), restart=True)
    supervise(app, "cursor_auto_orphan_scanner", lambda: orphan_scanner_loop(app))
    supervise(app, "cursor_auto_hop_cadence", lambda: hop_cadence_loop(app))
    supervise(app, "ulg_story_projector", lambda: ulg_story_projector_loop(app))
    supervise(app, "trigger_fire_loop", lambda: trigger_fire_loop(app))
    supervise(app, "lane_b_sweeper", lambda: lane_b_sweeper_loop(app))
    logger.info(
        "git-integration-worker started: version=%s port=%d source_repo=%s "
        "worker_id=%s startup_persistence=background",
        app.state.worker_version,
        cfg.port,
        cfg.source_repo,
        app.state.worker_id,
    )
    try:
        emit_git_worker_started(
            worker_id=app.state.worker_id,
            pid=os.getpid(),
            port=cfg.port,
            version=app.state.worker_version,
            started_at=worker_boot_ts,
            source_repo=str(cfg.source_repo),
            bind_host=cfg.host,
            build_sha=app.state.worker_version,
            health_url=f"http://{cfg.host}:{cfg.port}/health",
        )
    except Exception as exc:
        logger.warning("git_worker.started emission failed: %s", exc)
    try:
        yield
    finally:
        app.state.shutting_down = True
        clear_tool_op_invoker()
        shutdown_active_bridges()
        await shutdown_auto_jobs(app)
        for attr in (
            "startup_persistence_task",
            "stale_lease_sweeper",
            "cursor_auto_worker",
            "cursor_auto_orphan_scanner",
            "cursor_auto_hop_cadence",
            "ulg_story_projector",
            "trigger_fire_loop",
            "lane_b_sweeper",
        ):
            task = getattr(app.state, attr, None)
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        # Defense-in-depth cooperative drain on process shutdown: close admission
        # and wait (bounded) for in-flight mutating work to finish before exit.
        # Phase-2 manage drives the PRIMARY drain via the admin route ahead of
        # SIGTERM; this hook only covers a drain not already started (e.g. a
        # direct signal with no manage supervisor in the loop).
        if not controller.is_draining():
            controller.begin_drain(
                reason="lifespan_shutdown",
                intent_id=f"lifespan-{app.state.worker_id}",
                drain_epoch=controller.next_epoch(),
            )
        drained = await controller.wait_idle(timeout_s=_DRAIN_LIFESPAN_TIMEOUT_S)
        if not drained:
            logger.warning(
                "git-integration-worker lifespan drain timed out after %.1fs "
                "with %d op(s) still in flight",
                _DRAIN_LIFESPAN_TIMEOUT_S,
                controller.active_count(),
            )
        logger.info(
            "git-integration-worker stopped after %.1fs",
            time.monotonic() - app.state.worker_started_at,
        )


def create_app() -> FastAPI:
    """Build and return the git-integration-worker FastAPI application instance."""
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
    app.include_router(cursor_catalog_router)
    app.include_router(cursor_auto_router)
    app.include_router(admin_router)
    app.include_router(triggers_router)
    return app


app = create_app()
