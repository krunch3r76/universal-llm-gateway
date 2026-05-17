"""Public API surface for Remote federation FastAPI routers."""

from .router import create_inference_router

__all__ = ["create_inference_router"]
