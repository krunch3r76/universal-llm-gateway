"""
Universal LLM Gateway - Monitoring Tools

This package contains tools for monitoring and debugging the middleware system.

Available Tools:
- middleware_viewer: Real-time GUI for viewing middleware processing
- start_viewer: Launcher script for the middleware viewer
- test_monitor: Test script for UDP monitoring communication
"""

__version__ = "1.0.0"
__author__ = "Universal LLM Gateway Team"
__description__ = "Monitoring tools for Universal LLM Gateway middleware"

# Import main components for easier access
from .middleware_viewer import MiddlewareViewer

__all__ = [
    "MiddlewareViewer",
]
