"""GET /v1/models/{model_id} - Get specific model endpoint"""

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.model_registry.registry import ModelRegistry, normalize_model_id
from src.core.synthetic_models import SyntheticModel, SyntheticModelResolver
from src.routers.dependencies import get_model_registry
from src.routers.v1.models.get import build_comprehensive_model_info_for_synthetic
from src.schemas.openai_models import (
    ComprehensiveModelListResponse,
    OpenAIModelInfo,
    OpenAIModelListResponse,
)

router = APIRouter()


def find_synthetic_model(
    all_synthetic_models: list[SyntheticModel], model_id: str
) -> SyntheticModel | None:
    """
    Find a synthetic model by ID, with fallback to normalized ID matching.

    Single-pass lookup with priority:
    1. Exact match on synthetic_id (returns immediately)
    2. Normalized match (requested ID without -hybrid matches synthetic's normalized form)

    This handles the case where stargate normalizes model IDs (strips -hybrid suffix)
    before querying the gateway. The gateway registers models WITH the -hybrid suffix,
    so we need to match both exact and normalized forms.
    """
    normalized_requested = normalize_model_id(model_id)
    normalized_match: SyntheticModel | None = None

    for synthetic_model in all_synthetic_models:
        # Priority 1: Exact match - return immediately
        if synthetic_model.synthetic_id == model_id:
            return synthetic_model

        # Track first normalized match for fallback
        if normalized_match is None:
            normalized_synthetic = normalize_model_id(synthetic_model.synthetic_id)
            if normalized_synthetic == normalized_requested:
                normalized_match = synthetic_model

    # Priority 2: Return normalized match if no exact match found
    return normalized_match


@router.get("/models/{model_id}", tags=["OpenAI Compatible"])
async def get_model(
    model_id: str,
    include_all_fields: bool = Query(
        False,
        description="Include all model fields (default: basic OpenAI fields only)",
    ),
    available_only: bool = Query(
        True,
        description="Only return if model file path is available (prevents returning models with missing files)",
    ),
    model_registry: ModelRegistry = Depends(get_model_registry),
):
    """
    Get specific model information by synthetic model ID.

    Accepts synthetic model IDs (e.g., 'model-name-32768' or 'model-name-32768-cpu').
    Also accepts normalized IDs (without -hybrid suffix) which will match hybrid variants.
    Returns OpenAI-compatible format.
    Use include_all_fields=true to get comprehensive model information including all fields.

    By default, only returns models whose files are available on disk (available_only=true).
    This prevents Stargate from routing to models that cannot be loaded.
    """
    try:
        if not model_registry:
            raise HTTPException(status_code=500, detail="Model registry not available")

        # Get config from registry
        config = model_registry.model_loaders_config

        # Get all synthetic models and find the requested one
        all_synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        synthetic_model = find_synthetic_model(all_synthetic_models, model_id)
        if synthetic_model:
            # Check if model is enabled (if filtering requested)
            if not model_registry.is_model_enabled(synthetic_model.synthetic_id):
                raise HTTPException(
                    status_code=404,
                    detail=f"Model '{model_id}' is disabled.",
                )

            # Check file availability (if filtering requested)
            if available_only and not model_registry.is_model_path_available(
                synthetic_model.synthetic_id
            ):
                raise HTTPException(
                    status_code=404,
                    detail=f"Model '{model_id}' files not available on this gateway.",
                )

            if include_all_fields:
                # Return comprehensive model information
                model = build_comprehensive_model_info_for_synthetic(
                    synthetic_model, config, model_registry
                )
                return ComprehensiveModelListResponse(data=[model])
            else:
                # Return basic OpenAI model information
                model = OpenAIModelInfo(**synthetic_model.openai_fields)
                return OpenAIModelListResponse(data=[model])

        # Model not found
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found. Use /v1/models to see available models.",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting model: {str(e)}")
