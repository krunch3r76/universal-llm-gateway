"""GET /v1/models/extended - Extended models endpoint"""

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.model_registry.registry import ModelRegistry
from src.routers.dependencies import get_model_registry
from src.schemas.openai_models import ExtendedModelInfo, ExtendedModelListResponse

router = APIRouter()


def load_openai_models_config() -> dict[str, Any]:
    """Load OpenAI models configuration from model_loaders.yaml file"""
    config_path = (
        Path(__file__).parent.parent.parent.parent.parent
        / "config"
        / "model_loaders.yaml"
    )

    if not config_path.exists():
        raise FileNotFoundError(f"Model loaders config not found at {config_path}")

    with open(config_path) as f:
        config = yaml.safe_load(f)

    return config


def get_extended_models() -> list[ExtendedModelInfo]:
    """Get list of models with extended information including standardized metadata"""
    try:
        config = load_openai_models_config()
        models_data = config.get("models", {})

        models = []
        for model_id, model_config in models_data.items():
            if isinstance(model_config, dict):
                # Get model info section
                model_info = model_config.get("info", {})
                if model_info:
                    # Get OpenAI API fields from model info
                    openai_fields = model_info.get("openai_api_fields", {})

                    # Create extended model info
                    extended_info = {
                        # OpenAI API fields
                        "id": openai_fields.get("id", model_id),
                        "object": openai_fields.get("object", "model"),
                        "owned_by": openai_fields.get(
                            "owned_by", "universal-llm-gateway"
                        ),
                        "permission": openai_fields.get("permission", ["generate"]),
                        # Standardized model metadata
                        "input_schema": model_info.get("input_schema"),
                        "training_context_length": model_info.get(
                            "training_context_length"
                        ),
                        "supports_chat_history": model_info.get(
                            "supports_chat_history"
                        ),
                        "training_cutoff_year": model_info.get("training_cutoff_year"),
                        "model_family": model_info.get("family"),
                        "quantization": model_info.get("quant"),
                        "architecture": model_info.get("arch"),
                        "license": model_info.get("license"),
                        "parameters": model_info.get("parameters"),
                        "release_date": model_info.get("release_date"),
                        "description": model_info.get("description"),
                        "capabilities": model_info.get("capabilities"),
                        "safety_info": model_info.get("safety_info"),
                        # Loader-specific fields
                        "name": model_info.get("name"),
                        "format": model_info.get("format"),
                        "enabled": model_info.get("enabled"),
                        "path": model_info.get("path"),
                        # Resource usage
                        "ram_usage": model_info.get("ram_usage"),
                        "vram_usage": model_info.get("vram_usage"),
                    }

                    models.append(ExtendedModelInfo(**extended_info))

        return models

    except Exception:
        # Return empty list if config loading fails
        return []


@router.get(
    "/models/extended",
    response_model=ExtendedModelListResponse,
    tags=["Extended Models"],
)
async def list_extended_models(
    enabled_only: bool = Query(True, description="Only return enabled models"),
    model_registry: ModelRegistry = Depends(get_model_registry),
):
    """
    List available models with extended information including custom fields.
    This endpoint provides comprehensive model information for internal use.
    """
    try:
        models = get_extended_models()

        if enabled_only and model_registry:
            models = [m for m in models if model_registry.is_model_enabled(m.id)]

        return ExtendedModelListResponse(data=models)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error listing extended models: {str(e)}"
        )
