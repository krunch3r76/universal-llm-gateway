"""Main entry point for the Cortex API.

This module initializes the FastAPI application, sets up middleware,
includes all API routers, and defines startup/health check endpoints.
It also handles database migration on startup.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db import check_cortex_db, check_todos_db, cortex_conn, run_migrations
from src.routes import (
    assertions,
    boot,
    chunks,
    deadlines,
    entities,
    relationships,
    salience,
    session_journals,
    stats,
    surface_forms,
    todos,
)
from src.scoring import compact_access_log

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("cortex-api")

app = FastAPI(
    title="cortex-api",
    version="2.0.0",
    description="REST API for the Cortex knowledge graph. Sole access path to cortex.db and todos.db.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(todos.router)
app.include_router(entities.router)
app.include_router(assertions.router)
app.include_router(chunks.router)
app.include_router(surface_forms.router)
app.include_router(relationships.router)
app.include_router(deadlines.router)
app.include_router(session_journals.router)
app.include_router(stats.router)
app.include_router(salience.router)
app.include_router(boot.router)


@app.on_event("startup")
def _run_migrations() -> None:
    """Runs database migrations for cortex.db on application startup."""
    if not check_cortex_db():
        logger.warning("cortex.db not found — skipping migrations")
        return
    conn = cortex_conn()
    try:
        applied = run_migrations(conn)
        if applied:
            logger.info("Applied migrations: %s", applied)
    except Exception as e:
        logger.critical("Migration failed — cortex-api cannot start. Error: %s", e)
        # Depending on criticality, you might want to re-raise or sys.exit(1)
        # For now, just logging as critical to highlight the severity.
        raise
    finally:
        conn.close()


@app.on_event("startup")
def _compact_access_log() -> None:
    """Enforce 30-day TTL on entity_access_log, aggregating into weekly summaries."""
    if not check_cortex_db():
        return
    conn = cortex_conn()
    try:
        purged = compact_access_log(conn)
        if purged:
            logger.info("Access log compaction: purged %d old entries", purged)
    except Exception:
        logger.debug("Access log compaction skipped (table may not exist yet)")
    finally:
        conn.close()


@app.get("/health")
def health() -> dict[str, str]:
    """Returns the health status of the API and its database connections."""
    return {
        "status": "ok",
        "cortex_db": "found" if check_cortex_db() else "missing",
        "todos_db": "found" if check_todos_db() else "missing",
    }
