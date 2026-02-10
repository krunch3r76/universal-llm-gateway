"""
Middleware proxy core package for intelligent request processing.

Subpackages:
    - model/: Model loading, routing, polling, coordination
    - nonstreaming/: Non-streaming request processing pipeline
    - streaming/: Streaming response handling
    - common/: Cross-cutting helpers shared by both pipelines

Primary API:
    - ResourceAwareModelManager: Main entry point for model orchestration

Status Types (for external use):
    - GatewayStatusResult: Result of gateway status check
    - ModelLoadingStatus: Enum for load operation results
    - ModelStatus: Enum for model states on gateway
"""

# Primary public API
# Status types from control plane
from .control_plane import (
    GatewayStatusResult,
    ModelLoadingStatus,
    ModelStatus,
)
from .resource_aware_model_manager import (
    GatewayMetricsProvider,
    ResourceAwareModelManager,
)

__all__ = [
    # Primary API
    "ResourceAwareModelManager",
    "GatewayMetricsProvider",
    # Status types
    "GatewayStatusResult",
    "ModelLoadingStatus",
    "ModelStatus",
]
