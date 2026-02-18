"""GET /api/v1/models/{model_id}/requirements - Model resource requirements"""

from universal_logging import get_logger
from fastapi import APIRouter, Depends, HTTPException

from src.core.resources import resource_tracker
from src.routers.dependencies import get_model_registry
from src.schemas.gateway_status import (
    ModelCapabilities,
    ModelRequirementsResponse,
    ResourceRequirements,
)

router = APIRouter(prefix="/v1/models", tags=["Model Metadata"])
logger = get_logger(__name__)


@router.get("/{model_id}/requirements", response_model=ModelRequirementsResponse)
async def get_model_requirements(
    model_id: str,
    model_registry=Depends(get_model_registry),
):
    """
    Get resource requirements and capabilities for a specific model.

    Enables proxy to:
    - Determine if model fits on gateway
    - Calculate which models to unload
    - Plan resource allocation

    Args:
        model_id: Model identifier

    Returns:
        Model resource requirements and capabilities

    Raises:
        404: Model not found in registry
    """
    try:
        logger.debug(f"Getting requirements for model: {model_id}")

        # Check if model exists in registry
        model_config_key = model_registry.find_config_key_for_openai_id(model_id)
        if not model_config_key:
            raise HTTPException(
                status_code=404, detail=f"Model {model_id} not found in registry"
            )

        # Get model requirements from resource tracker
        requirements = resource_tracker.get_model_requirements(model_id)

        # Get model info for additional capabilities
        model_info = model_registry.get_model_info(model_id)

        # Determine context length from model info or requirements
        context_length = None
        if model_info:
            context_length = model_info.training_context_length
        if not context_length:
            context_length = requirements.get("context_length")

        # Determine quantization from requirements
        quantization = requirements.get("quantization")

        return ModelRequirementsResponse(
            model_id=model_id,
            resource_requirements=ResourceRequirements(
                vram_mb=requirements.get("vram_required_mb"),
                ram_mb=requirements.get("ram_required_mb"),
            ),
            capabilities=ModelCapabilities(
                context_length=context_length,
                quantization=quantization,
                supports_streaming=True,  # Assume all models support streaming
            ),
        )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Error getting requirements for model {model_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get model requirements: {str(e)}"
        )
