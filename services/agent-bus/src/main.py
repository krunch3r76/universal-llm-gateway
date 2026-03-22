from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db import init_db
from src.routes.messages import router as messages_router
from src.routes.threads import router as threads_router
from src.routes.turns import router as turns_router

logger = logging.getLogger("agent-bus")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    init_db()
    logger.info("agent-bus started")
    yield


app = FastAPI(
    title="agent-bus",
    version="2.0.0",
    description="Inter-agent message bus for Web Claude, API Claude, and Cursor Claude.",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(messages_router)
app.include_router(turns_router)
app.include_router(threads_router)
