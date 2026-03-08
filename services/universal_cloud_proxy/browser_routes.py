"""Browser routes for cloud model pricing UI and cost-oracle APIs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from universal_event_bus import EventBus
from universal_protocol import ErrorCode, error_envelope

from .browser import BrowserCatalogCache
from .catalog import CatalogManager
from .events import (
    CloudProxyBrowserCatalogRefreshed,
    CloudProxyBrowserCatalogRefreshFailed,
    CloudProxyBrowserModelLookupMiss,
    CloudProxyBrowserSelectCompleted,
    CloudProxyBrowserSelectFailed,
)
from .local_catalog import LocalCatalogCache


def _error_response(
    *,
    code: ErrorCode,
    message: str,
    status_code: int,
    retryable: bool = False,
    data: dict[str, object] | None = None,
) -> JSONResponse:
    """Build canonical error envelope response for browser endpoints."""
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(
            code=code,
            message=message,
            source="master",
            retryable=retryable,
            data=data or {},
        ),
    )


class SelectRequest(BaseModel):
    """Query for task-aware model selection."""

    tags: list[str] = Field(
        default_factory=list,
        description="Required capability tags (code, reasoning, fast, vision, general)",
    )
    exclude_tags: list[str] = Field(
        default_factory=list,
        description="Tags to exclude (e.g. exclude 'pro' for budget)",
    )
    min_context: int = Field(default=0, description="Minimum context window size")
    min_tier: int = Field(
        default=0,
        ge=0,
        le=3,
        description="Minimum quality tier (0=all, 1=low+, 2=mid+, 3=high only)",
    )
    min_prompt_cost: float | None = Field(
        default=None,
        description="Min prompt cost per million tokens (filter free tier)",
    )
    max_prompt_cost: float | None = Field(
        default=None, description="Max prompt cost per million tokens"
    )
    min_completion_cost: float | None = Field(
        default=None,
        description="Min completion cost per million tokens (filter free tier)",
    )
    max_completion_cost: float | None = Field(
        default=None, description="Max completion cost per million tokens"
    )
    modality_contains: str | None = Field(
        default=None, description="Required modality substring (e.g. 'image')"
    )
    providers: list[str] = Field(
        default_factory=list, description="Restrict to these providers"
    )
    count: int = Field(default=3, ge=1, le=20, description="Number of results")
    sort_by: str = Field(
        default="tier",
        description="Sort field: 'tier' (highest quality first, randomized within tier) or 'completion_cost', 'prompt_cost', 'context_length' (ascending)",
    )
    estimated_tokens: int | None = Field(
        default=None,
        ge=0,
        description="Optional token estimate for projected cost fields in response",
    )


def _project_cost(
    model: dict[str, object],
    estimated_tokens: int,
) -> dict[str, float]:
    """Project prompt and completion cost for a token estimate."""
    units = estimated_tokens / 1_000_000
    prompt_cost = float(model.get("prompt_cost", 0.0))
    completion_cost = float(model.get("completion_cost", 0.0))
    return {
        "projected_prompt_cost": round(prompt_cost * units, 8),
        "projected_completion_cost": round(completion_cost * units, 8),
    }


def register_browser_routes(
    app: FastAPI,
    *,
    static_dir: Path,
    get_browser_cache: Callable[[], BrowserCatalogCache | None],
    get_local_cache: Callable[[], LocalCatalogCache | None],
    get_catalog: Callable[[], CatalogManager | None],
    get_event_bus: Callable[[], EventBus | None],
    get_ui_status: Callable[[], tuple[bool, str]],
) -> None:
    """Register browser UI and pricing API routes on the cloud proxy app."""
    router = APIRouter()

    @router.get("/api/models")
    async def browser_models() -> JSONResponse:
        """Full OpenRouter catalog with pricing (auto-refreshes if stale)."""
        browser_cache = get_browser_cache()
        if browser_cache is None:
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Browser catalog is not initialized",
                status_code=500,
                data={"component": "browser_cache"},
            )

        try:
            await browser_cache.ensure_fresh()
            return JSONResponse(
                content={
                    "models": browser_cache.get_models(),
                    "count": browser_cache.model_count,
                }
            )
        except Exception as exc:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserCatalogRefreshFailed(
                        trigger="auto",
                        error=str(exc)[:300],
                    )
                )
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Failed to refresh browser catalog",
                status_code=502,
                retryable=True,
                data={"trigger": "auto"},
            )

    @router.post("/api/refresh")
    async def browser_refresh() -> JSONResponse:
        """Force re-fetch of the full OpenRouter catalog."""
        browser_cache = get_browser_cache()
        if browser_cache is None:
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Browser catalog is not initialized",
                status_code=500,
                data={"component": "browser_cache"},
            )

        try:
            count = await browser_cache.refresh()
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserCatalogRefreshed(
                        trigger="manual",
                        model_count=count,
                    )
                )
            return JSONResponse(content={"status": "refreshed", "count": count})
        except Exception as exc:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserCatalogRefreshFailed(
                        trigger="manual",
                        error=str(exc)[:300],
                    )
                )
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Manual browser catalog refresh failed",
                status_code=502,
                retryable=True,
                data={"trigger": "manual"},
            )

    @router.get("/api/models/{model_id:path}")
    async def browser_model_lookup(model_id: str) -> JSONResponse:
        """Single model pricing lookup — cost oracle for routing decisions."""
        browser_cache = get_browser_cache()
        if browser_cache is None:
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Browser catalog is not initialized",
                status_code=500,
                data={"component": "browser_cache"},
            )

        try:
            await browser_cache.ensure_fresh()
        except Exception as exc:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserCatalogRefreshFailed(
                        trigger="lookup_auto",
                        error=str(exc)[:300],
                    )
                )
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Failed to refresh browser catalog for lookup",
                status_code=502,
                retryable=True,
                data={"model_id": model_id},
            )

        model = browser_cache.lookup(model_id)
        if model is None:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserModelLookupMiss(model_id=model_id)
                )
            return _error_response(
                code=ErrorCode.MODEL_NOT_FOUND,
                message=f"Model not found: {model_id}",
                status_code=404,
                data={"model_id": model_id},
            )
        return JSONResponse(content=model)

    @router.post("/api/select")
    async def select_models(
        payload: dict[str, object] | None = Body(default=None),
    ) -> JSONResponse:
        """Select best models for a task based on capability and context.

        Filters the full OpenRouter catalog by tags, cost, context length,
        and modality, returning top candidates sorted by requested strategy.
        Default strategy is quality-tier-first with intra-tier randomization.
        """
        try:
            req = SelectRequest.model_validate(payload or {})
        except ValidationError as exc:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserSelectFailed(error=str(exc)[:300])
                )
            return _error_response(
                code=ErrorCode.INVALID_REQUEST,
                message="Invalid /api/select payload",
                status_code=422,
                data={"validation_errors": exc.errors()},
            )

        browser_cache = get_browser_cache()
        if browser_cache is None:
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Browser catalog is not initialized",
                status_code=500,
                data={"component": "browser_cache"},
            )

        try:
            await browser_cache.ensure_fresh()
        except Exception as exc:
            event_bus = get_event_bus()
            if event_bus is not None:
                await event_bus.publish_async(
                    CloudProxyBrowserSelectFailed(error=str(exc)[:300])
                )
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message=f"Catalog refresh failed: {exc}",
                status_code=502,
                retryable=True,
            )

        local_cache = get_local_cache()
        extra_models: list[dict[str, Any]] | None = None
        if local_cache is not None:
            await local_cache.ensure_fresh()
            local_models = local_cache.get_models()
            if local_models:
                extra_models = local_models

        selected = browser_cache.select(
            tags=req.tags or None,
            exclude_tags=req.exclude_tags or None,
            min_context=req.min_context,
            min_tier=req.min_tier,
            min_prompt_cost=req.min_prompt_cost,
            max_prompt_cost=req.max_prompt_cost,
            min_completion_cost=req.min_completion_cost,
            max_completion_cost=req.max_completion_cost,
            modality_contains=req.modality_contains,
            providers=req.providers or None,
            count=req.count,
            sort_by=req.sort_by,
            extra_models=extra_models,
        )
        if req.estimated_tokens is not None:
            selected = [
                {**model, **_project_cost(model, req.estimated_tokens)}
                for model in selected
            ]
        event_bus = get_event_bus()
        if event_bus is not None:
            await event_bus.publish_async(
                CloudProxyBrowserSelectCompleted(
                    selected_count=len(selected),
                    tags=req.tags,
                    exclude_tags=req.exclude_tags,
                    sort_by=req.sort_by,
                    min_context=req.min_context,
                    modality_contains=req.modality_contains,
                    max_completion_cost=req.max_completion_cost,
                    auto_excluded_multimodal=False,
                )
            )
        return JSONResponse(
            content={
                "models": selected,
                "count": len(selected),
                "query": req.model_dump(exclude_none=True),
            }
        )

    @router.get("/catalog/pricing")
    async def catalog_pricing() -> JSONResponse:
        """Pricing for the configured (filtered) model set — for routing integration."""
        catalog = get_catalog()
        if catalog is None:
            return _error_response(
                code=ErrorCode.UNEXPECTED_ERROR,
                message="Catalog manager is not initialized",
                status_code=500,
                data={"component": "catalog_manager"},
            )
        return JSONResponse(content=catalog.get_all_models_with_pricing())

    @router.get("/")
    def browser_index() -> Response:
        """Serve the model browser UI."""
        is_ready, error_message = get_ui_status()
        if not is_ready:
            return _error_response(
                code=ErrorCode.RESOURCE_UNAVAILABLE,
                message="Browser UI assets are unavailable",
                status_code=503,
                retryable=True,
                data={"reason": error_message},
            )
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-cache"},
        )

    app.include_router(router)
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
