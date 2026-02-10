"""GET /api/v1/model_info/aliases - Model aliases endpoint"""

from fastapi import APIRouter, Depends, HTTPException

from src.core.model_registry import ModelRegistry
from src.routers.dependencies import get_model_registry

router = APIRouter(prefix="/v1/model_info", tags=["Debug & Administration"])


@router.get("/aliases", tags=["Debug & Administration"])
async def list_model_aliases(
    model_registry: ModelRegistry | None = Depends(get_model_registry),
):
    """
    List model aliases

    Returns a mapping of model aliases to their target model IDs.
    This is a custom endpoint beyond OpenAI compatibility.
    """
    if not model_registry:
        raise HTTPException(status_code=500, detail="Model registry not initialized")

    aliases = model_registry.list_aliases()

    return {"aliases": aliases, "count": len(aliases)}
