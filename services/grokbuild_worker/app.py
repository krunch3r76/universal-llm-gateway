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

import asyncio
import os
import subprocess
import time
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from build_results import prune_spool
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from grokbuild.auth_notifier import (
    clear_notification_latch,
    notify_if_needed,
    notify_if_needed_async,
)
from grokbuild.auth_probe import AuthStatus, probe_grok_auth
from grokbuild.events_core import register_uds_publisher
from universal_logging import get_logger

from services.grokbuild_worker.config import WorkerConfig, load_config
from services.grokbuild_worker.events import (
    emit_auth_required,
    emit_auth_restored,
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


async def _periodic_auth_probe(
    app: FastAPI,
    cfg: WorkerConfig,
    interval_s: int,
) -> None:
    """Periodic T3 auth probe: update app.state.grok_auth_status every interval_s.

    Runs until cancelled by lifespan shutdown.  Updates grok_auth_status so
    /health reflects the current auth state without blocking the request path.
    Emits grokbuild.auth.required on transition to non-OK;
    emits grokbuild.auth.restored on transition back to OK.
    """
    prior_status: AuthStatus | None = getattr(app.state, "grok_auth_status", None)
    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            return

        try:
            result = probe_grok_auth()
        except Exception:  # noqa: BLE001
            logger.exception("Periodic auth probe raised unexpectedly")
            continue

        app.state.grok_auth_status = result.status

        if result.status != AuthStatus.OK and (
            prior_status == AuthStatus.OK or prior_status is None
        ):
            if getattr(app.state, "grok_auth_failed_at", None) is None:
                app.state.grok_auth_failed_at = time.monotonic()
            logger.warning(
                "Periodic probe: grok auth %s: %s", result.status, result.detail
            )
            try:
                await notify_if_needed_async(
                    sidecar_dir=cfg.sidecar_dir,
                    agent_bus_url=cfg.agent_bus_url,
                    agent_bus_token=cfg.agent_bus_token,
                    notify_slug=cfg.grok_auth_notify_slug,
                    notify_to=cfg.grok_auth_notify_to,
                    debounce_h=cfg.grok_auth_debounce_h,
                    trigger="periodic",
                    grok_auth_dir=str(cfg.grok_auth_dir),
                    deploy_shape=cfg.deploy_shape,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "auth_notifier.notify_if_needed_async failed (periodic)"
                )
            try:
                emit_auth_required(
                    reason_code=result.status,
                    grok_auth_dir=str(cfg.grok_auth_dir),
                    deploy_shape=cfg.deploy_shape,
                    trigger="periodic",
                    debounce_key="",
                )
            except Exception:  # noqa: BLE001
                logger.exception("Failed to emit grokbuild.auth.required (periodic)")
        elif (
            result.status == AuthStatus.OK
            and prior_status is not None
            and prior_status != AuthStatus.OK
        ):
            logger.info("Periodic probe: grok auth restored")
            failed_at = getattr(app.state, "grok_auth_failed_at", None)
            downtime_s = time.monotonic() - failed_at if failed_at is not None else 0.0
            app.state.grok_auth_failed_at = None
            try:
                clear_notification_latch(cfg.sidecar_dir)
                emit_auth_restored(str(cfg.grok_auth_dir), downtime_s)
            except Exception:  # noqa: BLE001
                logger.exception("Failed on auth restore (periodic)")
            from grokbuild.auth_notifier import _cortex_todo_close

            try:
                _cortex_todo_close(cfg.cortex_api_url, cfg.cortex_api_token)
            except Exception:  # noqa: BLE001
                logger.exception("_cortex_todo_close failed (periodic restore)")

        prior_status = result.status


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

    # T1: startup auth probe — sets app.state.grok_auth_status so _evaluate()
    # and the /health endpoint can surface grok_auth without a blocking
    # subprocess call on every poll.
    auth_result = probe_grok_auth()
    app.state.grok_auth_status = auth_result.status
    app.state.grok_auth_failed_at: float | None = (
        time.monotonic() if auth_result.status != AuthStatus.OK else None
    )
    if auth_result.status != AuthStatus.OK:
        logger.warning(
            "grokbuild-worker: startup auth probe %s: %s",
            auth_result.status,
            auth_result.detail,
        )
        debounce_key_out: list[str] = []
        notified = False
        try:
            notified = notify_if_needed(
                sidecar_dir=cfg.sidecar_dir,
                agent_bus_url=cfg.agent_bus_url,
                agent_bus_token=cfg.agent_bus_token,
                notify_slug=cfg.grok_auth_notify_slug,
                notify_to=cfg.grok_auth_notify_to,
                debounce_h=cfg.grok_auth_debounce_h,
                trigger="startup",
                grok_auth_dir=str(cfg.grok_auth_dir),
                deploy_shape=cfg.deploy_shape,
                debounce_key_out=debounce_key_out,
            )
        except Exception:  # noqa: BLE001
            logger.exception("auth_notifier.notify_if_needed failed at startup")
        if notified:
            from grokbuild.auth_notifier import _cortex_todo_open

            try:
                _cortex_todo_open(cfg.cortex_api_url, cfg.cortex_api_token)
            except Exception:  # noqa: BLE001
                logger.exception("_cortex_todo_open failed at startup")
        try:
            emit_auth_required(
                reason_code=auth_result.status,
                grok_auth_dir=str(cfg.grok_auth_dir),
                deploy_shape=cfg.deploy_shape,
                trigger="startup",
                debounce_key=debounce_key_out[0] if debounce_key_out else "",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to emit grokbuild.auth.required (startup)")

    checks = _evaluate(cfg, app.state)
    # Option A (OQ-1): grok_auth is informational — it must NOT contribute to the
    # worker.degraded signal or worker.started.degraded_checks. Auth expiry is
    # surfaced via grokbuild.auth.required + the agent-bus notification instead.
    degraded = [
        name
        for name, status in checks.items()
        if status != "ok" and name != "grok_auth"
    ]
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

    # T3: optional periodic auth probe task.
    # ∀ interval > 0: asyncio task polls grok models every interval seconds
    # and updates app.state.grok_auth_status.  Default 0 = disabled.
    _probe_interval_s = int(os.environ.get("GROKBUILD_AUTH_PROBE_INTERVAL_S", "0"))
    _periodic_task: asyncio.Task[None] | None = None
    if _probe_interval_s > 0:
        _periodic_task = asyncio.create_task(
            _periodic_auth_probe(app, cfg, _probe_interval_s),
            name="grokbuild-auth-probe",
        )
        logger.info(
            "grokbuild-worker: periodic auth probe enabled, interval=%ds",
            _probe_interval_s,
        )

    try:
        yield
    finally:
        if _periodic_task is not None and not _periodic_task.done():
            _periodic_task.cancel()
            try:
                await _periodic_task
            except asyncio.CancelledError:
                pass
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
