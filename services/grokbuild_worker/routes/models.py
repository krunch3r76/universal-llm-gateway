"""``GET /api/v1/grokbuild/models`` — list available grok models.

Returns the contents of ``MODEL_REGISTRY`` from ``grokbuild.constants``
as a structured list so callers can inspect per-model capability flags
without reading source. No auth or request body required — this endpoint
is intentionally lightweight and safe to call from any context.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from grokbuild.constants import MODEL_REGISTRY
from pydantic import BaseModel

from services.grokbuild_worker.events import (
    GrokbuildModelsListed,
    publish_nowait,
)

router = APIRouter(prefix="/api/v1/grokbuild", tags=["grokbuild-models"])


class _ModelEntry(BaseModel):
    """One row of the ``GET /models`` response."""

    id: str
    supports_reasoning_effort: bool
    supports_effort: bool
    supports_subagents: bool
    internal_multi_agent: bool
    default_reasoning_effort: str | None
    notes: str


class ModelsResponse(BaseModel):
    """Response shape for ``GET /api/v1/grokbuild/models``."""

    models: list[_ModelEntry]


@router.get(
    "/models",
    response_model=ModelsResponse,
    status_code=200,
    summary="List grok models with their capability flags.",
)
async def list_models() -> JSONResponse:
    """Return all registered grok models with their capability flags."""
    t0 = time.monotonic()
    models: list[dict[str, Any]] = [
        {"id": name, **dataclasses.asdict(caps)}
        for name, caps in MODEL_REGISTRY.items()
    ]
    duration_s = time.monotonic() - t0
    publish_nowait(GrokbuildModelsListed(count=len(models), duration_s=duration_s))
    return JSONResponse(content={"models": models})
