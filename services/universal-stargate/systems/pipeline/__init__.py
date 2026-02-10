"""
Pipeline module - multi-model workflow orchestration.

The pipeline system is fully domain-agnostic:
- core/: Domain-agnostic infrastructure (schemas, handlers, DAG, prompts)
- All domain handlers loaded from user handlers directory or entry points

Typical usage:
    from systems.pipeline import PipelineExecutor, PipelineRegistry

    registry = PipelineRegistry(search_paths=["config"])
    registry.load()

    executor = PipelineExecutor(registry, request_executor, proxy)
    response = await executor.execute(context)
"""

# Core exports
from .core.dag import DAGBuilder
from .core.execution import DAGExecutor
from .core.executor import PipelineExecutor
from .core.handlers import (
    HandlerRegistry,
    PipelineContext,
    StepHandler,
    StepOutput,
)
from .core.prompts import PromptBuilder, get_prompt_builder
from .core.schemas import (
    FragmentRef,
    PipelineOptions,
    PipelineSpec,
    StepConfig,
)

# Plugin API for external domains
from .plugins import (
    discover_plugins,
    register_domain,
    register_domain_handler,
)

# Registry
from .registry import PipelineRegistry

# Schemas for registry compatibility
from .schemas import ModelRef, SharedModels, SharedPrompts

__all__ = [
    # Core
    "DAGBuilder",
    "DAGExecutor",
    "FragmentRef",
    "HandlerRegistry",
    "PipelineContext",
    "PipelineExecutor",
    "PipelineOptions",
    "PipelineRegistry",
    "PipelineSpec",
    "PromptBuilder",
    "StepHandler",
    "StepOutput",
    "StepConfig",
    "get_prompt_builder",
    # Registry schemas
    "ModelRef",
    "SharedModels",
    "SharedPrompts",
    # Plugin API
    "discover_plugins",
    "register_domain",
    "register_domain_handler",
]
