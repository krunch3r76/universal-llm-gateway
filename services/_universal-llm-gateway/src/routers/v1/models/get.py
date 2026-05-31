"""GET /v1/models - List models endpoint"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from universal_logging import get_logger

from src.core.model_registry.registry import ModelRegistry
from src.core.synthetic_models import SyntheticModelResolver
from src.routers.dependencies import get_model_registry
from src.schemas.openai_models import (
    ComprehensiveModelInfo,
    ComprehensiveModelListResponse,
    OpenAIModelInfo,
    OpenAIModelListResponse,
)

logger = get_logger(__name__)
router = APIRouter()


class ListModelsQuery(BaseModel):
    """Query parameters for listing models"""

    enabled_only: bool = Field(True, description="Only return enabled models")
    available_only: bool = Field(
        True,
        description="Only return models with available file paths (prevents listing models whose files are missing)",
    )
    include_all_variants: bool = Field(
        True,
        description="Include all context length variants (now defaults to True - Stargate handles filtering)",
    )
    include_all_fields: bool = Field(
        False,
        description="Include all model fields (default: basic OpenAI fields only)",
    )


def build_comprehensive_model_info_for_synthetic(
    synthetic_model, config: dict[str, Any], model_registry: ModelRegistry = None
) -> ComprehensiveModelInfo:
    """Build comprehensive model info for a synthetic model ID"""
    # Resolve synthetic ID to get base model config and profile
    result = SyntheticModelResolver.get_model_config_for_synthetic_id(
        config, synthetic_model.synthetic_id
    )

    if not result:
        # Fallback to basic info if resolution fails
        return ComprehensiveModelInfo(
            id=synthetic_model.synthetic_id,
            object="model",
            owned_by=synthetic_model.openai_fields.get(
                "owned_by", "universal-llm-gateway"
            ),
            permission=synthetic_model.openai_fields.get("permission", ["generate"]),
        )

    base_model_config, profile_config = result
    model_info = base_model_config.get("info", {})
    openai_fields = model_info.get("openai_api_fields", {})

    # Get the actual loader config that will be used internally for this model_id
    # This ensures consistency with what workers actually receive
    if model_registry:
        loader_config = (
            model_registry.get_model_loader_config(synthetic_model.synthetic_id) or {}
        )
    else:
        # Fallback: compile it the same way as the registry does
        loader_config = base_model_config.get("base_loader", {}).copy()
        if profile_config and profile_config.get("loader"):
            loader_config.update(profile_config["loader"])

    # Get context length from the profile for this specific model
    context_length = profile_config.get(
        "context_length", synthetic_model.context_length
    )

    # Determine loader_type from loader_config and model format (critical for Stargate routing)
    model_format = model_info.get("format", "")
    loader_type = "unknown"

    if model_format == "whisper":
        # Whisper models use device field to determine CPU/GPU
        device = loader_config.get("device", "cpu")
        loader_type = "whisper_gpu" if device == "cuda" else "whisper_cpu"
    elif "n_gpu_layers" in loader_config:
        # GGUF models use n_gpu_layers
        n_gpu = loader_config.get("n_gpu_layers", 0)
        if n_gpu == 0:
            loader_type = "llama_cpp_cpu"
        elif n_gpu == -1:
            loader_type = "llama_cpp_gpu"
        else:
            loader_type = "llama_cpp_hybrid"
    elif model_format in ("hf", "awq", "gptq"):
        # vLLM models are always GPU
        loader_type = "vllm_gpu"

    # Build info dict - only include fields needed for API clients
    # Profile selection is internal to synthetic ID generation
    info_dict = {
        # OpenAI API fields
        "id": synthetic_model.synthetic_id,
        "object": openai_fields.get("object", "model"),
        "owned_by": openai_fields.get("owned_by", "universal-llm-gateway"),
        "permission": openai_fields.get("permission", ["generate"]),
        # Basic model fields
        "name": model_info.get("name"),
        "format": model_info.get("format"),
        "enabled": model_info.get("enabled"),
        "path": model_info.get("path"),
        # Resource usage from profile
        "ram_usage": profile_config.get("resources", {}).get("ram_mb"),
        "vram_usage": profile_config.get("resources", {}).get("vram_mb"),
        "loader_type": loader_type,
        # Standardized metadata (from capabilities)
        "training_context_length": model_info.get("training_context_length")
        or (model_info.get("capabilities") or {})
        .get("limits", {})
        .get("max_context_length"),
        "input_schema": model_info.get("input_schema"),
        "context_length": context_length,
        "training_cutoff_year": model_info.get("training_cutoff_year"),
        "model_family": model_info.get("family"),
        "quantization": model_info.get("quant"),
        "architecture": model_info.get("arch"),
        "license": (model_info.get("capabilities") or {})
        .get("provenance", {})
        .get("license")
        or model_info.get("license"),
        "parameters": model_info.get("parameters"),
        "release_date": model_info.get("release_date"),
        "description": model_info.get("description"),
        "capabilities": model_info.get("capabilities"),
        # Loader configuration - the actual config used by workers
        "loader_config": loader_config,
    }

    # Create ComprehensiveModelInfo with context_length
    return ComprehensiveModelInfo(**info_dict)


def get_openai_models(
    config: dict, include_all_variants: bool = True
) -> list[OpenAIModelInfo]:
    """
    Get list of OpenAI-compatible models from configuration.

    Uses synthetic model IDs with context lengths and CPU variants.

    NOTE: As of the Stargate-side activated contexts filtering feature,
    Gateway now returns ALL synthetic models by default. Stargate handles
    filtering based on activated_gpu_contexts/activated_cpu_contexts from
    the catalog. This separation of concerns means:
    - Gateway is simpler - just returns all synthetic model variants
    - Stargate controls what to expose to clients
    - Local catalog changes don't require Gateway reload

    The include_all_variants parameter is kept for backward compatibility
    but now defaults to True.
    """
    try:
        # Always return all synthetic models - Stargate handles filtering
        # based on activated contexts from catalog
        synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        models = []
        for synthetic_model in synthetic_models:
            models.append(OpenAIModelInfo(**synthetic_model.openai_fields))

        return models

    except Exception as e:
        logger.error("Error generating OpenAI models list: %s", e, exc_info=True)
        return []


def get_comprehensive_models(
    config: dict, include_all_variants: bool = True
) -> list[ComprehensiveModelInfo]:
    """
    Get list of comprehensive model information from configuration.

    Uses synthetic model IDs with context lengths and CPU variants.
    Returns full model information including all fields.

    NOTE: As of the Stargate-side activated contexts filtering feature,
    Gateway now returns ALL synthetic models by default. Stargate handles
    filtering based on activated_gpu_contexts/activated_cpu_contexts.

    The include_all_variants parameter is kept for backward compatibility
    but now defaults to True.
    """
    try:
        # Always return all synthetic models - Stargate handles filtering
        synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        models = []
        for synthetic_model in synthetic_models:
            comprehensive_info = build_comprehensive_model_info_for_synthetic(
                synthetic_model, config, None
            )
            models.append(comprehensive_info)

        return models

    except Exception as e:
        logger.error("Error generating comprehensive models list: %s", e, exc_info=True)
        return []


@router.get("/models", tags=["OpenAI Compatible"])
async def list_models(
    query: ListModelsQuery = Depends(),
    model_registry: ModelRegistry = Depends(get_model_registry),
):
    """
    List available models using synthetic model IDs.

    Returns synthetic model IDs with explicit context lengths (e.g., 'model-name-32768').
    CPU variants are suffixed with '-cpu' (e.g., 'model-name-32768-cpu').

    NOTE: As of the Stargate-side activated contexts filtering feature, Gateway
    now returns ALL synthetic model variants. Stargate fetches activated contexts
    from the catalog (/api/v1/catalog) and filters to only expose activated models.
    This separation of concerns means:
    - Gateway is simpler - returns exhaustive list
    - Stargate controls exposure based on activated_gpu_contexts/activated_cpu_contexts
    - Local catalog changes take effect without Gateway restart

    Query parameters:
    - enabled_only: Filter to only enabled models
    - available_only: Filter to models with accessible file paths (prevents "phantom" models)
    - include_all_variants: Kept for backward compatibility (now defaults to True)
    - include_all_fields: Return comprehensive model information
    """
    try:
        if not model_registry:
            raise HTTPException(status_code=500, detail="Model registry not available")

        # Get config from registry
        config = model_registry.model_loaders_config

        # Use canonical filtering method to get model IDs
        # This ensures consistency with WebSocket INIT message
        filtered_model_ids = model_registry.get_available_synthetic_model_ids(
            enabled_only=query.enabled_only, available_only=query.available_only
        )

        # Convert filtered IDs back to synthetic model objects for response building
        all_synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        filtered_synthetic_models = [
            sm for sm in all_synthetic_models if sm.synthetic_id in filtered_model_ids
        ]

        # Get models based on include_all_fields parameter
        if query.include_all_fields:
            # Return comprehensive model information with model_registry for accurate loader_config
            models = []
            for synthetic_model in filtered_synthetic_models:
                comprehensive_info = build_comprehensive_model_info_for_synthetic(
                    synthetic_model, config, model_registry
                )
                models.append(comprehensive_info)

            return ComprehensiveModelListResponse(data=models)
        else:
            # Return basic OpenAI model information
            models = [
                OpenAIModelInfo(**sm.openai_fields) for sm in filtered_synthetic_models
            ]

            return OpenAIModelListResponse(data=models)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing models: {str(e)}")
