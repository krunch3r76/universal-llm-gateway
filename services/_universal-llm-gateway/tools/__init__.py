"""
Universal LLM Gateway - Monitoring Tools

This package contains tools for monitoring and debugging the middleware system.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "1.0.0"
__author__ = "Universal LLM Gateway Team"
__description__ = "Monitoring tools for Universal LLM Gateway middleware"

if TYPE_CHECKING:
    from .middleware_viewer import MiddlewareViewer

__all__ = [
    "MiddlewareViewer",
]


def __getattr__(name: str) -> object:
    if name == "MiddlewareViewer":
        from .middleware_viewer import MiddlewareViewer

        return MiddlewareViewer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
