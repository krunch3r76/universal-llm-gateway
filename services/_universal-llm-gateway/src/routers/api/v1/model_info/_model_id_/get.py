"""GET /api/v1/model_info/{model_id} - Get specific model info endpoint"""

from fastapi import APIRouter, Depends, HTTPException

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_metadata_adapter, get_model_registry
from src.routers.model_metadata_adapter import ModelMetadataAdapter
from src.schemas.model_info import ModelInfo
from src.utils.model_utils import convert_metadata_to_model_info

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])


@router.get("/{model_id}", response_model=ModelInfo, tags=["Debug & Administration"])
async def get_model_info(
    model_id: str,
    registry: ModelRegistry | None = Depends(get_model_registry),
    metadata_adapter: ModelMetadataAdapter | None = Depends(get_model_metadata_adapter),
) -> ModelInfo:
    """Get comprehensive model information including middleware metadata

    This endpoint provides all the information middleware needs:
    - Chat template status and content
    - Model type detection
    - Parameter defaults
    - Middleware configuration
    - Supported parameters
    """
    # Get comprehensive metadata from the service
    model_metadata = metadata_adapter.get_comprehensive_model_metadata(model_id)
    if not model_metadata:
        raise HTTPException(status_code=404, detail=f"Model {model_id} not found")

    # Convert to response schema using centralized utility
    return convert_metadata_to_model_info(model_metadata)
