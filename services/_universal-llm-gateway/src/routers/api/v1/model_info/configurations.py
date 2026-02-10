"""GET /api/v1/model_info/configurations - Model configurations endpoint"""

from fastapi import APIRouter, Depends, HTTPException
from universal_logging import get_logger

from src.routers.dependencies import get_model_metadata_adapter
from src.routers.model_metadata_adapter import ModelMetadataAdapter
from src.schemas.model_info import ModelConfigurationsResponse
from src.utils.model_utils import convert_metadata_to_model_info

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])


@router.get(
    "/configurations",
    response_model=ModelConfigurationsResponse,
    tags=["Debug & Administration"],
)
async def get_all_model_configurations(
    metadata_adapter: ModelMetadataAdapter | None = Depends(get_model_metadata_adapter),
) -> ModelConfigurationsResponse:
    """Get configurations for all models at once

    This endpoint is optimized for middleware initialization to get all
    model configurations in a single API call instead of multiple requests.
    """
    logger.info("ENDPOINT: /configurations called")
    if not metadata_adapter:
        raise HTTPException(
            status_code=500, detail="Model metadata adapter not initialized"
        )

    logger.info("ENDPOINT: Calling get_all_models_metadata()")
    all_models_metadata = metadata_adapter.get_all_models_metadata()
    logger.info(
        f"ENDPOINT: get_all_models_metadata() returned {len(all_models_metadata)} models"
    )

    # Convert to response format using centralized utility
    models = {}
    for model_id, model_metadata in all_models_metadata.items():
        models[model_id] = convert_metadata_to_model_info(model_metadata)

    return ModelConfigurationsResponse(models=models)
