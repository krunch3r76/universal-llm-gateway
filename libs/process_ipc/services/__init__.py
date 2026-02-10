"""
Service layer for process IPC package.

Provides resource monitoring services for single worker management.

ARCHITECTURAL CHANGE: Logging layer removed (Phase 1: wire-automatic-truncation).
Use universal_logging directly:
    from universal_logging import get_logger
    logger = get_logger(__name__)  # Auto-initializes
"""

from .simple_resource_monitor import SimpleResourceMonitor

__all__ = [
    "SimpleResourceMonitor",
]
