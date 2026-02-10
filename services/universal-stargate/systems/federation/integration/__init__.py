"""
Federation integration package.

Re-exports FederationIntegration and module-level functions.
"""

from .core import FederationIntegration
from .lifecycle import (
    get_federation_integration,
    init_federation,
    shutdown_federation,
)

__all__ = [
    "FederationIntegration",
    "get_federation_integration",
    "init_federation",
    "shutdown_federation",
]
