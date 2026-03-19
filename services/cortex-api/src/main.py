from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db import check_cortex_db, check_todos_db
from src.routes import assertions, deadlines, entities, session_journals, todos

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("cortex-api")

app = FastAPI(
    title="cortex-api",
    version="1.0.0",
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
app.include_router(deadlines.router)
app.include_router(session_journals.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "cortex_db": "found" if check_cortex_db() else "missing",
        "todos_db": "found" if check_todos_db() else "missing",
    }
