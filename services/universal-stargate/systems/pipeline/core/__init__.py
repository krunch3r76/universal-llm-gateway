"""
Domain-agnostic pipeline core.

Provides:
- Generic schemas (PipelineSpec, StepConfig, PipelineOptions)
- Generic prompt rendering (PromptBuilder with dot-notation)
- Handler protocol and registry (StepHandler, HandlerRegistry)
- DAG-based execution (DAGBuilder, DAGExecutor)
- Fragment composition (FragmentLoader)
- Domain routing (DomainRouter)

Domain-specific logic lives in domains/{domain}/.
"""

from .conditions import (
    ConditionEvaluator,
    StepOutputProxy,
    evaluate_condition,
    get_condition_evaluator,
)
from .dag import DAGBuilder
from .domain_router import DomainRouter, get_domain_router
from .events import CheckpointFailed, CheckpointLoaded, CheckpointSaved
from .execution import DAGExecutor
from .executor import PipelineExecutor
from .fragments import FragmentLoader, get_fragment_loader
from .handlers import (
    HandlerRegistry,
    PipelineContext,
    StepHandler,
    StepOutput,
)
from .migration import SchemaMigrator
from .prompts import PromptBuilder, get_prompt_builder
from .schemas import (
    FragmentRef,
    InputBinding,
    OutputBinding,
    OutputDeclaration,
    PipelineOptions,
    PipelineSpec,
    ReadsFrom,
    SourceInput,
    StepConfig,
    StepInputs,
    SubPipelineSpec,
)
from .validation import PipelineValidator

__all__ = [
    # Schemas
    "FragmentRef",
    "PipelineOptions",
    "PipelineSpec",
    "SubPipelineSpec",
    # Phase 1 schemas
    "InputBinding",
    "OutputBinding",
    "OutputDeclaration",
    "ReadsFrom",
    "SourceInput",
    "StepConfig",
    "StepInputs",
    # Handlers
    "HandlerRegistry",
    "PipelineContext",
    "StepHandler",
    "StepOutput",
    # Execution
    "DAGBuilder",
    "DAGExecutor",
    "PipelineExecutor",
    # Prompts
    "PromptBuilder",
    "get_prompt_builder",
    # Fragments
    "FragmentLoader",
    "get_fragment_loader",
    # Routing
    "DomainRouter",
    "get_domain_router",
    # Conditions
    "ConditionEvaluator",
    "StepOutputProxy",
    "evaluate_condition",
    "get_condition_evaluator",
    # Validation & Migration (Phase 1)
    "PipelineValidator",
    "SchemaMigrator",
    # Events (Phase 3)
    "CheckpointSaved",
    "CheckpointLoaded",
    "CheckpointFailed",
]
