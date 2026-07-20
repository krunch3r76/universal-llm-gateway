"""Catalog read-only HTTP routes for merged model catalog inspection and listing.

Serves full catalog, per-model detail, and summary listings without local catalog
writes or reload side effects.
"""

from typing import Any

from fastapi import HTTPException, Query

try:
    from ......core.catalog import get_catalog_loader
except ImportError:
    from src.core.catalog import get_catalog_loader

from .deps import logger, router
from .schemas import (
    CatalogResponse,
    ModelEntryResponse,
    ModelSummary,
    ModelSummaryListResponse,
)


@router.get("", response_model=CatalogResponse)
async def get_catalog(
    include_models: bool = Query(
        True, description="Include models section in response"
    ),
) -> CatalogResponse:
    """
    Get the full merged catalog (static + dynamic).

    Note: Transformations are NOT included - they are Stargate's domain.
    Gateway is a pure passthrough and does not handle request modifications.

    Args:
        include_models: Whether to include the models section (default: True)

    Returns:
        CatalogResponse with catalog data (models only)
    """
    try:
        loader = get_catalog_loader()
        catalog = loader.load()

        return CatalogResponse(
            catalog_version=catalog.get("catalog_version", "1.0"),
            catalog_type=catalog.get("catalog_type", "merged"),
            schema_version=catalog.get("schema_version", 2),
            models=catalog.get("models", {}) if include_models else {},
        )
    except Exception as e:
        logger.error(f"Failed to load catalog: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")


@router.get("/models", response_model=dict[str, Any])
async def get_catalog_models(
    format_filter: str | None = Query(
        None, description="Filter by model format (gguf, awq, hf, gptq)"
    ),
) -> dict[str, Any]:
    """
    Get all models from the catalog.

    Args:
        format_filter: Optional filter by model format

    Returns:
        Dictionary of model entries keyed by model_id
    """
    try:
        loader = get_catalog_loader()

        if format_filter:
            model_ids = loader.list_models_by_format(format_filter)
            models = {mid: loader.get_model(mid) for mid in model_ids}
        else:
            catalog = loader.load()
            models = catalog.get("models", {})

        return {"models": models, "count": len(models)}
    except Exception as e:
        logger.error(f"Failed to load models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")


@router.get("/models/list", response_model=ModelSummaryListResponse)
async def list_catalog_models_simple(
    format_filter: str | None = Query(
        None, description="Filter by model format (gguf, awq, hf, gptq)"
    ),
) -> ModelSummaryListResponse:
    """
    Get a simple list of catalog models with ID, filename, and format.

    Useful for finding the correct model_id for measurement jobs.
    Unlike /v1/models which shows synthetic model IDs with context suffixes,
    this returns the base catalog model IDs.

    Args:
        format_filter: Optional filter by model format

    Returns:
        List of ModelSummary with model_id, filename, hf_repo, format
    """
    try:
        loader = get_catalog_loader()

        if format_filter:
            model_ids = loader.list_models_by_format(format_filter)
        else:
            catalog = loader.load()
            model_ids = list(catalog.get("models", {}).keys())

        summaries: list[ModelSummary] = []
        for mid in sorted(model_ids):
            model = loader.get_model(mid)
            if not model:
                continue

            metadata = model.get("metadata", {})
            download = model.get("download", {})
            hf_info = download.get("huggingface", {})

            hf_file = hf_info.get("file")
            hf_repo = hf_info.get("repo")

            if hf_file:
                filename = hf_file
            elif hf_repo:
                filename = hf_repo.split("/")[-1]
            else:
                filename = mid

            summaries.append(
                ModelSummary(
                    model_id=mid,
                    filename=filename,
                    hf_repo=hf_repo,
                    format=metadata.get("format", "unknown"),
                    display_name=metadata.get("display_name") or metadata.get("name"),
                    description=metadata.get("description"),
                )
            )

        return ModelSummaryListResponse(models=summaries, count=len(summaries))
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list models: {e}")


@router.get("/models/{model_id}", response_model=ModelEntryResponse)
async def get_catalog_model(model_id: str) -> ModelEntryResponse:
    """
    Get a specific model entry from the catalog.

    Args:
        model_id: Model identifier

    Returns:
        ModelEntryResponse with model data

    Raises:
        HTTPException: 404 if model not found
    """
    try:
        loader = get_catalog_loader()
        model = loader.get_model(model_id)

        if not model:
            raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")

        return ModelEntryResponse(
            model_id=model_id,
            schema_name=model.get("schema", ""),
            metadata=model.get("metadata", {}),
            download=model.get("download", {}),
            loader=model.get("loader", {}),
            devices=model.get("devices", {}),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load model {model_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load catalog: {e}")
