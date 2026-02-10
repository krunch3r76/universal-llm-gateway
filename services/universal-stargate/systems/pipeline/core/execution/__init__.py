"""
Pipeline execution components.

Provides DAG execution with HTTP-based model invocation.

PipelineContext (provided by executor in later phases) contains:
- source: SourceInput - Pipeline input data
- options: dict - Runtime options from request
- outputs: dict[str, StepOutput] - Completed step outputs
"""

from .checkpoint import (
    CheckpointBackend,
    CheckpointData,
    CheckpointManager,
    FilesystemCheckpointBackend,
)
from .critical_path import (
    calculate_critical_path,
    calculate_step_depths,
    find_parallel_siblings,
)
from .errors import (
    BindingResolutionError,
    HandlerTimeoutError,
    InputTypeMismatchError,
    InvalidNamespaceError,
    MapPartialFailureError,
    OutputValidationError,
    PipelineError,
    StepTimeoutError,
)
from .executor import DAGExecutor
from .map_reduce import (
    MapExecutor,
    MapIterationContext,
    MapJsonAccessor,
    MapOutputCollection,
)
from .model_tracker import ModelUsageTracker
from .protocols import BatchRouterProtocol
from .proxy_client import ProxyClient, ProxyClientConfig, ProxyClientError
from .resolver import (
    MapNamespaceHandler,
    NamespaceHandler,
    NamespaceResolver,
    OptionsNamespaceHandler,
    PipelineContextProtocol,
    SourceNamespaceHandler,
    StepOutputNamespaceHandler,
    traverse_path,
)
from .retry import RetryPolicy, execute_with_retry
from .step_wrapper import execute_step_with_wrappers
from .timeout import execute_with_handler_timeout, execute_with_step_timeout

__all__ = [
    # Executor
    "DAGExecutor",
    # Critical path analysis
    "calculate_critical_path",
    "calculate_step_depths",
    "find_parallel_siblings",
    # Model tracking
    "ModelUsageTracker",
    # Protocols
    "BatchRouterProtocol",
    # ProxyClient (Phase 2.7)
    "ProxyClient",
    "ProxyClientConfig",
    "ProxyClientError",
    # Errors (Phase 1)
    "PipelineError",
    "BindingResolutionError",
    "OutputValidationError",
    "InputTypeMismatchError",
    "InvalidNamespaceError",
    "StepTimeoutError",
    "HandlerTimeoutError",
    "MapPartialFailureError",
    # Resolution (Phase 1)
    "NamespaceResolver",
    "NamespaceHandler",
    "PipelineContextProtocol",
    "SourceNamespaceHandler",
    "OptionsNamespaceHandler",
    "StepOutputNamespaceHandler",
    "MapNamespaceHandler",
    "traverse_path",
    # Retry & Timeout (Phase 2)
    "RetryPolicy",
    "execute_with_retry",
    "execute_with_step_timeout",
    "execute_with_handler_timeout",
    "execute_step_with_wrappers",
    # Checkpoint (Phase 3)
    "CheckpointBackend",
    "CheckpointData",
    "CheckpointManager",
    "FilesystemCheckpointBackend",
    # Map/Reduce (Phase 4)
    "MapExecutor",
    "MapOutputCollection",
    "MapIterationContext",
    "MapJsonAccessor",
]
