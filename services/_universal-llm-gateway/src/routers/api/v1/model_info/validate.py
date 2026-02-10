"""GET /api/v1/model_info/validate - Model validation endpoint"""

from fastapi import APIRouter, Depends, HTTPException

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_registry
from src.schemas.model_info import ModelValidationReport

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])


@router.get(
    "/validate", response_model=ModelValidationReport, tags=["Debug & Administration"]
)
async def validate_models(
    model_registry: ModelRegistry | None = Depends(get_model_registry),
):
    """
    Validate model files

    Checks if all enabled model files exist and are accessible.
    This is a custom endpoint for system administration.
    """
    if not model_registry:
        raise HTTPException(status_code=500, detail="Model registry not initialized")

    try:
        validation_report = model_registry.validate_model_files()
        return validation_report

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error validating models: {str(e)}"
        )
