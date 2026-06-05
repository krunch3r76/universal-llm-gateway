"""Pipeline error hierarchy with structured serialization.

∀ error: error.to_dict() → JSON-compatible dict for API responses

Package layout (modularized from the former single ``errors.py`` module; the
public import path is unchanged — import the names below from
``...core.execution.errors``):
- ``pipeline_error`` — the :class:`PipelineError` ABC (``retryable`` default
  ``False``, abstract ``to_dict``).
- ``binding`` — ``BindingResolutionError`` / ``InputTypeMismatchError`` /
  ``InvalidNamespaceError`` (input binding + namespace resolution).
- ``validation`` — ``OutputValidationError`` (handler output-contract).
- ``timeout`` — ``StepTimeoutError`` / ``HandlerTimeoutError`` (retryable).
- ``dispatch_frontier`` — ``RemoteMcpUnsupportedError`` /
  ``UnknownPipelineOptionsError`` / ``AgentModelMismatchError`` /
  ``EmptyCompletionError`` / ``FrontierDispatchExhaustedError`` (each carries a
  stable ``code`` for ``_normalize_pipeline_exception``).
- ``map_reduce`` — ``MapPartialFailureError`` (lazy ``IterationStatus``).
- ``concurrency`` — ``ConcurrencyLockTimeoutError``.
"""

from .binding import (
    BindingResolutionError,
    InputTypeMismatchError,
    InvalidNamespaceError,
)
from .concurrency import ConcurrencyLockTimeoutError
from .dispatch_frontier import (
    AgentModelMismatchError,
    CapabilityCatalogMissError,
    CapabilityKnobRejectedError,
    EmptyCompletionError,
    FrontierDispatchExhaustedError,
    RemoteMcpUnsupportedError,
    UnknownPipelineOptionsError,
)
from .map_reduce import MapPartialFailureError
from .pipeline_error import PipelineError
from .timeout import HandlerTimeoutError, StepTimeoutError
from .validation import OutputValidationError

__all__ = [
    "PipelineError",
    "BindingResolutionError",
    "OutputValidationError",
    "InputTypeMismatchError",
    "InvalidNamespaceError",
    "StepTimeoutError",
    "HandlerTimeoutError",
    "RemoteMcpUnsupportedError",
    "UnknownPipelineOptionsError",
    "AgentModelMismatchError",
    "CapabilityKnobRejectedError",
    "CapabilityCatalogMissError",
    "EmptyCompletionError",
    "FrontierDispatchExhaustedError",
    "MapPartialFailureError",
    "ConcurrencyLockTimeoutError",
]
