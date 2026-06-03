"""
Step handler protocol and execution context.

Defines the interface all step handlers must implement.

Invariants:
- ∀ handler: handler.step_type ∈ str
- ∀ handler: handler.execute() returns StepOutput (never writes to context)
- ∀ step_id ∈ context.outputs: step completed successfully
- Only DAGExecutor writes to context.outputs (single-writer)
"""

from __future__ import annotations

from .handler_contract import AbstractStepHandler, StepHandler
from .pipeline_context import PipelineContext
from .step_output import MapIterationState, StepOutput

__all__ = [
    "AbstractStepHandler",
    "MapIterationState",
    "PipelineContext",
    "StepHandler",
    "StepOutput",
]
