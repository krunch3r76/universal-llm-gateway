"""
Control plane for model orchestration.

Manages model placement (where work runs) and model lifecycle
    (load/unload/wait/coordination).
Replaces the broad core/model/ domain with explicit subdomains.
"""

# Re-export public API from model lifecycle
from .model_lifecycle.coordination import (
    GlobalModelLoadCoordinator,
    LoadCoordinationResult,
    ModelLoadCoordinator,
)
from .model_lifecycle.format_detection import (
    FormatDetectionError,
    InferenceEngine,
    detect_format_from_metadata,
    detect_format_from_path,
    engine_supports_batching,
    get_engine_for_model,
    get_replication_policy_for_model,
    supports_multi_instance_per_gateway,
)
from .model_lifecycle.replication import (
    REPLICATION_POLICIES,
    ModelFormat,
    ReplicationPolicy,
    UnknownFormatError,
    format_supports_multi_instance_per_gateway,
    get_replication_policy,
)
from .model_lifecycle.status import GatewayStatusResult, ModelLoadingStatus, ModelStatus
from .types import (
    AttemptImmediateRoute,
    ConfigHelper,
    MissingResourceRequirementsError,
    ResourceManagerProvider,
    ResourceRequirements,
    ResourceRequirementsProvider,
    SchedulerConfigProvider,
)

__all__ = [
    # Status types
    "GatewayStatusResult",
    "ModelLoadingStatus",
    "ModelStatus",
    # Coordination
    "GlobalModelLoadCoordinator",
    "ModelLoadCoordinator",
    "LoadCoordinationResult",
    # Replication
    "ModelFormat",
    "ReplicationPolicy",
    "REPLICATION_POLICIES",
    "get_replication_policy",
    "format_supports_multi_instance_per_gateway",
    # Format detection
    "FormatDetectionError",
    "InferenceEngine",
    "detect_format_from_metadata",
    "detect_format_from_path",
    "get_engine_for_model",
    "get_replication_policy_for_model",
    "supports_multi_instance_per_gateway",
    "engine_supports_batching",
    # Replication errors
    "UnknownFormatError",
    # Types
    "AttemptImmediateRoute",
    "ConfigHelper",
    "MissingResourceRequirementsError",
    "ResourceManagerProvider",
    "ResourceRequirements",
    "ResourceRequirementsProvider",
    "SchedulerConfigProvider",
]
