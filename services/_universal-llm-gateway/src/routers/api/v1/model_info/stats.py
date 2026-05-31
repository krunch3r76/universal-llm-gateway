"""GET /api/v1/model_info/stats - Model statistics endpoint"""

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_metadata_adapter, get_model_registry
from src.routers.model_metadata_adapter import ModelMetadataAdapter

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])
logger = get_logger(__name__)


@router.get("/stats", tags=["Debug & Administration"])
async def get_model_stats(
    model_registry: ModelRegistry | None = Depends(get_model_registry),
    metadata_adapter: ModelMetadataAdapter | None = Depends(get_model_metadata_adapter),
):
    """
    Get model statistics

    Returns statistics about the model registry.
    This is a custom endpoint for monitoring.
    """
    if not model_registry:
        raise HTTPException(status_code=500, detail="Model registry not initialized")

    if not metadata_adapter:
        raise HTTPException(
            status_code=500, detail="Model metadata adapter not initialized"
        )

    try:
        model_counts = model_registry.get_model_count()

        # Get format breakdown from model_loaders_config
        format_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}

        models_data = model_registry.model_loaders_config.get("models", {})
        for model_id, model_data in models_data.items():
            if not isinstance(model_data, dict):
                continue
            info = model_data.get("info", {})
            format_type = info.get("format") or "unknown"
            format_counts[format_type] = format_counts.get(format_type, 0) + 1

            model_config = model_registry.get_model_config(model_id)
            model_type = metadata_adapter.get_model_type_from_config(
                model_id, model_config or {}
            )
            type_counts[model_type] = type_counts.get(model_type, 0) + 1

        return {
            "total_models": model_counts["total"],
            "enabled_models": model_counts["enabled"],
            "disabled_models": model_counts["disabled"],
            "format_breakdown": format_counts,
            "type_breakdown": type_counts,
        }

    except Exception as e:
        logger.exception("Error getting model stats")
        raise HTTPException(
            status_code=500, detail=f"Error getting model stats: {str(e)}"
        )
