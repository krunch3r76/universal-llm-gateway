"""Scheduler endpoints package"""

from fastapi import APIRouter

from .status import router as status_router

# Create main scheduler router
router = APIRouter(prefix="/scheduler", tags=["scheduler"])

# Include sub-routers
router.include_router(status_router)
