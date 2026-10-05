"""Cortex API — FastAPI application factory.

Exposes ``create_app()`` so the service can be started by the ``server``
module or directly by uvicorn (``cortex_store.main:create_app``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from deploy_identity.code_version import process_age_s, resolve_code_version
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from ulg_routing_headers import (
    attach_idempotency_to_non_get_stamped_routes,
    router_carries_xmcp_stamp,
    ulg_routing_headers,
)
from universal_logging import get_logger
from universal_workspace import get_workspace_root

from . import embeddings as cortex_embeddings
from . import vector_store
from .db import check_cortex_db, cortex_conn, run_migrations
from .routes import (
    assertions,
    boot,
    claims_burst,
    close_draft,
    deadlines,
    dispatch,
    doctrine,
    documents,
    edges,
    entities,
    entity_status,
    extraction_runs,
    gated,
    graph,
    graph_imprint,
    graph_recall,
    reaper,
    reflective_journal,
    relationships,
    resolve,
    salience,
    seat_claims,
    session_handoff,
    session_journals,
    skills,
    staging,
    stats,
    subgraph,
    surface_forms,
    tags,
    todo_audit,
    todo_retrieval,
)
from .scoring import compact_access_log
from .skill_graph_drift_monitor import run_skill_graph_drift_monitor

logger = get_logger("cortex-api")

_DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b-q8-0-8192"
_SOURCE_SYNC_STAMP = Path.home() / ".gateway" / "cortex-api.source_sync_stamp"


def _resolve_workspace_root() -> Path | None:
    for raw in (
        os.environ.get("ULG_WORKSPACE_ROOT"),
        os.environ.get("WORKSPACE_ROOT"),
    ):
        if raw:
            candidate = Path(raw).expanduser()
            if candidate.is_dir():
                return candidate.resolve()
    try:
        return get_workspace_root()
    except RuntimeError:
        return None


def _read_deploy_identity() -> dict[str, str | None]:
    """P1b source-identity fields exposed on GET /health for deploy-state gate.

    ``source_synced_at`` is only reported when a sync stamp records one. Absent a
    stamp there was no observed sync, and ``deploy_mode`` says ``unstamped``
    rather than claiming ``source_synced``. ``source_ref``/``source_tree_hash``
    describe the checkout on disk at request time — deliberately distinct from
    ``code_version``, which describes the loaded process.
    """
    source_synced_at: str | None = None
    source_sync_generation: str | None = None
    if _SOURCE_SYNC_STAMP.is_file():
        lines = _SOURCE_SYNC_STAMP.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            source_synced_at = lines[0].strip() or None
        if len(lines) > 1:
            source_sync_generation = lines[1].strip() or None
    deploy_mode = "source_synced" if source_synced_at else "unstamped"

    source_ref: str | None = None
    source_tree_hash: str | None = None
    workspace = _resolve_workspace_root()
    if workspace is not None and (workspace / ".git").is_dir():
        try:
            head = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if head.returncode == 0:
                source_ref = f"git:{head.stdout.strip()}"
            tree = subprocess.run(
                ["git", "-C", str(workspace), "rev-parse", "HEAD^{tree}"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if tree.returncode == 0:
                source_tree_hash = tree.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            logger.debug("deploy identity git probe failed", exc_info=True)

    return {
        "deploy_mode": deploy_mode,
        "source_synced_at": source_synced_at,
        "source_ref": source_ref,
        "source_tree_hash": source_tree_hash,
        "source_sync_generation": source_sync_generation,
    }


def _init_vector_subsystem() -> None:
    """Configure embedding client + ChromaDB vector store at startup.

    Best-effort: failures are logged but do not prevent cortex-api from
    serving. Search will degrade to FTS5-only if this fails.
    """
    model_id = os.environ.get("CORTEX_EMBEDDING_MODEL", _DEFAULT_EMBEDDING_MODEL)
    try:
        cortex_embeddings.configure(model_id)
    except Exception:
        logger.warning("Failed to configure embedding model", exc_info=True)
        return

    db_path_str = os.environ.get(
        "CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db")
    )
    db_dir = Path(db_path_str).parent
    try:
        vector_store.init_vector_store(db_dir)
    except Exception:
        logger.warning("Failed to initialize vector store", exc_info=True)


def create_app(*, db_path: str | None = None) -> FastAPI:
    """Build the FastAPI application.

    If *db_path* is provided it overrides ``CORTEX_DB_PATH``.
    """
    if db_path is not None:
        os.environ["CORTEX_DB_PATH"] = db_path

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if check_cortex_db():
            conn = cortex_conn()
            try:
                applied = run_migrations(conn)
                if applied:
                    logger.info("Applied migrations: %s", applied)
            except Exception:
                logger.critical(
                    "Migration failed — cortex-api cannot start", exc_info=True
                )
                raise
            finally:
                conn.close()

            conn = cortex_conn()
            try:
                purged = compact_access_log(conn)
                if purged:
                    logger.info("Access log compaction: purged %d old entries", purged)
            except Exception:
                logger.debug("Access log compaction skipped (table may not exist yet)")
            finally:
                conn.close()

            _init_vector_subsystem()
        else:
            logger.warning("cortex.db not found — skipping migrations")

        monitor_enabled = os.environ.get(
            "CORTEX_SKILL_GRAPH_DRIFT_MONITOR", "true"
        ).lower() in ("true", "1", "yes")
        monitor_task: asyncio.Task[None] | None = None
        if monitor_enabled:
            monitor_task = asyncio.create_task(run_skill_graph_drift_monitor())
            logger.info("skill-graph drift monitor started")

        logger.info("cortex-api started")
        try:
            yield
        finally:
            if monitor_task is not None:
                monitor_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor_task

    app = FastAPI(
        title="cortex-api",
        version="3.0.0",
        description="REST API for the Cortex knowledge graph.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _include(router: APIRouter, **kwargs: Any) -> None:
        # Bind § "How served OpenAPI declares them": one dependency on every
        # router that carries an x-mcp stamp. Idempotency-Key is per non-GET
        # stamped operation, not router-wide, so GET stamps stay exact.
        if router_carries_xmcp_stamp(router):
            attach_idempotency_to_non_get_stamped_routes(router)
            extra = list(kwargs.pop("dependencies", []) or [])
            extra.append(Depends(ulg_routing_headers))
            kwargs["dependencies"] = extra
        app.include_router(router, **kwargs)

    _include(claims_burst.router)
    _include(entities.router)
    _include(entity_status.router)
    _include(assertions.router)
    _include(edges.router)
    _include(surface_forms.router)
    _include(relationships.router)
    _include(deadlines.router)
    _include(session_journals.router)
    _include(session_handoff.router)
    _include(stats.router)
    _include(salience.router)
    _include(boot.router)
    _include(skills.router)
    _include(todo_retrieval.router)
    _include(todo_audit.router)
    _include(extraction_runs.router)
    _include(staging.router)
    _include(gated.router)
    _include(documents.router)
    _include(doctrine.router)
    _include(resolve.router)
    _include(tags.router)
    _include(graph.router)
    _include(graph_imprint.router)
    _include(graph_recall.router)
    _include(close_draft.router)
    _include(subgraph.router)
    _include(reaper.router)
    _include(reflective_journal.router)
    from .routes.substrate_endeavor_views import router as substrate_endeavor_views_router
    from .routes.substrate_s6_batch7 import router as substrate_s6_batch7_router
    from .routes.substrate_s6_batch8 import router as substrate_s6_batch8_router
    from .routes.substrate_s6_batch9 import router as substrate_s6_batch9_router
    from .routes.substrate_sidecar_writes import router as substrate_sidecar_writes_router

    _include(substrate_sidecar_writes_router)
    _include(substrate_endeavor_views_router)
    _include(substrate_s6_batch7_router)
    _include(substrate_s6_batch8_router)
    _include(substrate_s6_batch9_router)
    _include(seat_claims.router)
    from .routes.frictions import router as frictions_router

    _include(frictions_router)
    from .routes.triage import router as triage_router

    _include(triage_router, prefix="/assertions")
    _include(dispatch.router)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(  # noqa: ANN202
        request: Request, exc: Exception
    ) -> JSONResponse:
        # Sole-maintainer environment — prefer surfacing exception type/message
        # over a bare "Internal Server Error". Expensive debug loops on opaque
        # 500s are the explicit motivator (todo:session-close-friction-audit P1,
        # F1: NameError in audit gate hidden behind generic 500). Disable by
        # setting CORTEX_DEBUG=0 if a deployment ever needs the redacted form.
        logger.error(
            "Unhandled exception in %s %s",
            request.method,
            request.url.path,
            exc_info=True,
        )
        if os.environ.get("CORTEX_DEBUG", "1") == "0":
            return JSONResponse(
                status_code=500, content={"detail": "Internal Server Error"}
            )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "error": type(exc).__name__,
                "message": str(exc),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str | float | int | None]:
        return {
            "status": "ok",
            "cortex_db": "found" if check_cortex_db() else "missing",
            "code_version": resolve_code_version(),
            "pid": os.getpid(),
            "process_age_s": process_age_s(),
            **_read_deploy_identity(),
        }

    return app


app = create_app()
