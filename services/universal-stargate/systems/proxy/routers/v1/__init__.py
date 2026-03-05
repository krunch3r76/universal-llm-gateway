"""OpenAI API v1 endpoints package.

Aggregates and mounts all v1-compatible routers under the `/v1` prefix.
"""

from fastapi import APIRouter

from systems.audio.api.stream import router as audio_router
from systems.graphics.api.generations import router as images_router

from .chat_completion import router as chat_router
from .embeddings import router as embeddings_router
from .model_profiles import router as model_profiles_router
from .models import router as models_router
from .pipeline_estimate import router as pipeline_estimate_router

# Create main v1 router
router = APIRouter(prefix="/v1", tags=["v1"])

# Include sub-routers
router.include_router(audio_router)
router.include_router(images_router)
router.include_router(chat_router)
router.include_router(embeddings_router)
router.include_router(models_router)
router.include_router(model_profiles_router)
router.include_router(pipeline_estimate_router)
