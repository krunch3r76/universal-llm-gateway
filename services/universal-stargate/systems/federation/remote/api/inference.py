"""Backward-compatible import surface for the federation inference router."""

from .router import create_inference_router

__all__ = ["create_inference_router"]
