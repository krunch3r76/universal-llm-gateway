"""
Pipeline registry - loads and manages pipeline configurations.

Validates all configurations at load time (fail-fast).
Pipeline loading filtered by model availability across connected gateways.
"""

from .core import PipelineRegistry
from .validator import PipelineConfigError

__all__ = ["PipelineRegistry", "PipelineConfigError"]
