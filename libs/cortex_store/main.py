"""Cortex API — FastAPI application factory.

Exposes ``create_app()`` so the service can be started by the ``server``
module or directly by uvicorn (``cortex_store.main:create_app``).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from universal_logging import get_logger

from . import embeddings as cortex_embeddings
from . import vector_store
from .db import check_cortex_db, cortex_conn, run_migrations
from .routes import (
    assertions,
    boot,
    deadlines,
    dispatch,
    documents,
    edges,
    entities,
    entity_status,
    extraction_runs,
    gated,
    graph,
    reaper,
    reflective_journal,
    relationships,
    resolve,
    salience,
    session_handoff,
    session_journals,
    skills,
    stats,
    subgraph,
    surface_forms,
    tags,
    todo_audit,
    todo_retrieval,
)
from .scoring import compact_access_log

logger = get_logger("cortex-api")

_DEFAULT_EMBEDDING_MODEL = "qwen3-embedding-8b-q8-0-8192"


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

        logger.info("cortex-api started")
        yield

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

    app.include_router(entities.router)
    app.include_router(entity_status.router)
    app.include_router(assertions.router)
    app.include_router(edges.router)
    app.include_router(surface_forms.router)
    app.include_router(relationships.router)
    app.include_router(deadlines.router)
    app.include_router(session_journals.router)
    app.include_router(session_handoff.router)
    app.include_router(stats.router)
    app.include_router(salience.router)
    app.include_router(boot.router)
    app.include_router(skills.router)
    app.include_router(todo_retrieval.router)
    app.include_router(todo_audit.router)
    app.include_router(extraction_runs.router)
    app.include_router(gated.router)
    app.include_router(documents.router)
    app.include_router(resolve.router)
    app.include_router(tags.router)
    app.include_router(graph.router)
    app.include_router(subgraph.router)
    app.include_router(reaper.router)
    app.include_router(reflective_journal.router)
    from .routes.triage import router as triage_router

    app.include_router(triage_router, prefix="/assertions")
    app.include_router(dispatch.router)

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
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "cortex_db": "found" if check_cortex_db() else "missing",
        }

    return app


app = create_app()
