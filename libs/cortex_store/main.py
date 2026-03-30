"""Cortex API — FastAPI application factory.

Exposes ``create_app()`` so the service can be started by the ``server``
module or directly by uvicorn (``cortex_store.main:create_app``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import check_cortex_db, cortex_conn, run_migrations
from .routes import (
    assertions,
    boot,
    chunks,
    deadlines,
    edges,
    entities,
    entity_status,
    gated,
    relationships,
    salience,
    session_journals,
    stats,
    surface_forms,
)
from .scoring import compact_access_log

logger = logging.getLogger("cortex-api")


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
        else:
            logger.warning("cortex.db not found — skipping migrations")

        logger.info("cortex-api started")
        yield

    app = FastAPI(
        title="cortex-api",
        version="2.0.0",
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
    app.include_router(chunks.router)
    app.include_router(surface_forms.router)
    app.include_router(relationships.router)
    app.include_router(deadlines.router)
    app.include_router(session_journals.router)
    app.include_router(stats.router)
    app.include_router(salience.router)
    app.include_router(boot.router)
    app.include_router(gated.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "cortex_db": "found" if check_cortex_db() else "missing",
        }

    return app


app = create_app()
