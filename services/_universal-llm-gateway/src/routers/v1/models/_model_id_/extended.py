"""GET /v1/models/{model_id}/extended - Extended model info endpoint"""

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException

from src.core.model_registry.registry import ModelRegistry
from src.routers.dependencies import get_model_registry
from src.schemas.openai_models import ExtendedModelInfo

router = APIRouter()


def load_openai_models_config() -> dict[str, Any]:
    """Load OpenAI models configuration from model_loaders.yaml file"""
    config_path = (
        Path(__file__).parent.parent.parent.parent.parent.parent
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
                # Get OpenAI API fields and metadata
                openai_fields = model_config.get("openai_api_fields", {})
                metadata = model_config.get("metadata", {})

                # Create extended model info
                extended_info = {
                    # OpenAI API fields
                    "id": openai_fields.get("id", model_id),
                    "object": openai_fields.get("object", "model"),
                    "owned_by": openai_fields.get("owned_by", "universal-llm-gateway"),
                    "permission": openai_fields.get("permission", ["generate"]),
                    # Standardized model metadata (from capabilities)
                    "input_schema": metadata.get("input_schema"),
                    "training_context_length": metadata.get("training_context_length")
                    or (metadata.get("capabilities") or {})
                    .get("limits", {})
                    .get("max_context_length"),
                    "training_cutoff_year": metadata.get("training_cutoff_year"),
                    "model_family": metadata.get("model_family"),
                    "quantization": metadata.get("quantization"),
                    "architecture": metadata.get("architecture"),
                    "license": (metadata.get("capabilities") or {})
                    .get("provenance", {})
                    .get("license")
                    or metadata.get("license"),
                    "parameters": metadata.get("parameters"),
                    "release_date": metadata.get("release_date"),
                    "description": metadata.get("description"),
                    "capabilities": metadata.get("capabilities"),
                    # Loader-specific fields
                    "name": model_config.get("name"),
                    "format": model_config.get("format"),
                    "enabled": model_config.get("enabled"),
                    "path": model_config.get("path"),
                    "ram_usage": model_config.get("ram_usage"),
                    "vram_usage": model_config.get("vram_usage"),
                }

                models.append(ExtendedModelInfo(**extended_info))

        return models

    except Exception:
        # Return empty list if config loading fails
        return []


@router.get(
    "/models/{model_id}/extended",
    response_model=ExtendedModelInfo,
    tags=["Extended Models"],
)
async def get_extended_model(
    model_id: str, model_registry: ModelRegistry = Depends(get_model_registry)
):
    """
    Get extended model information including custom fields

    Returns comprehensive information about a specific model including
    OpenAI API fields, loader-specific fields, and custom fields.
    """
    try:
        models = get_extended_models()

        # Find the requested model
        for model in models:
            if model.id == model_id:
                return model

        # Model not found
        raise HTTPException(
            status_code=404,
            detail=f"Model '{model_id}' not found. Use /v1/models/extended to see available models.",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error getting extended model: {str(e)}"
        )
