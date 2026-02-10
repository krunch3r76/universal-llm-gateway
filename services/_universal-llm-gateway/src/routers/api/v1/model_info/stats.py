"""GET /api/v1/model_info/stats - Model statistics endpoint"""

from fastapi import APIRouter, Depends, HTTPException

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_metadata_adapter, get_model_registry
from src.routers.model_metadata_adapter import ModelMetadataAdapter

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])


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

        # Get format breakdown
        format_counts = {}
        type_counts = {}

        for model_metadata in model_registry.models_to_metadata.values():
            # Count by format
            format_type = model_metadata.format
            format_counts[format_type] = format_counts.get(format_type, 0) + 1

            # Count by model type (from metadata)
            # Get the full model config to access the family/type info
            model_config = model_registry.get_model_config(model_metadata.name)
            model_type = metadata_adapter.get_model_type_from_config(
                model_metadata.name, model_config
            )
            type_counts[model_type] = type_counts.get(model_type, 0) + 1

        return {
            "total_models": model_counts["total"],
            "enabled_models": model_counts["enabled"],
            "loaded_models": model_counts["loaded"],
            "aliases_count": model_counts["aliases"],
            "format_breakdown": format_counts,
            "type_breakdown": type_counts,
        }

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting model stats: {str(e)}"
        )
