"""Public Stargate configuration package exports.

This package shadows the previous single-file module and preserves the same
import surface while splitting configuration concerns into focused modules.
"""

from .config import StargateConfig
from .types import DebugEventConfig

__all__ = [
    "StargateConfig",
    "DebugEventConfig",
]
