"""Model ID resolution for chat completions.

Responsibility: Parse and validate model IDs, preserving synthetic IDs (context suffixes).
"""

from fastapi import HTTPException
from model_id import ModelId

from src.core.model_registry import ModelRegistry


def resolve_model_id(
    model_override: str | None,
    request_model: str | None,
    model_registry: ModelRegistry | None,
) -> ModelId:
    """
    Resolve and validate model ID from request.

    ∀ request: validation uses base_id; routing uses full synthetic ID.

    Args:
        model_override: Query param model override
        request_model: Model ID from request body
        model_registry: Registry for validation

    Returns:
        ModelId: Parsed model with full ID preserved

    Raises:
        HTTPException: If model not specified or not found
    """
    selected_model = model_override or request_model
    if not selected_model:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "message": "Model must be specified",
                    "type": "invalid_request_error",
                    "code": "missing_model",
                    "param": "model",
                }
            },
        )

    # Parse into ModelId (preserves full ID like "model-65536")
    model_id = ModelId.parse(selected_model)

    # Validate existence using base_id (for synthetic IDs)
    if model_registry:
        config = model_registry.get_model_config(model_id.base_id)
        if not config:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "message": f"Model '{selected_model}' not found in registry",
                        "type": "invalid_request_error",
                        "code": "model_not_found",
                        "param": "model",
                    }
                },
            )

    return model_id

