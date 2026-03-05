"""
Modularize pipeline handlers.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from universal_stargate.systems.pipeline.core.router import HandlerRouter


def register_handlers(router: "HandlerRouter") -> None:
    """Required entry point. No domain handlers for this pipeline."""
