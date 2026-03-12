"""Administrative API v1 endpoints package"""

from fastapi import APIRouter

from .gateways import router as gateways_router
from .profiles import router as profiles_router
from .rag_scopes import router as rag_scopes_router
from .report_model import router as report_model_router
from .v1.cancel import router as cancel_router

# Create main API v1 router
router = APIRouter(prefix="/api/v1", tags=["api"])

# Include sub-routers
router.include_router(profiles_router)
router.include_router(gateways_router)
router.include_router(report_model_router)
router.include_router(cancel_router)
router.include_router(rag_scopes_router)
