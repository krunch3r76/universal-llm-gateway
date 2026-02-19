"""
Pipeline schemas - exports from core.
"""

from pydantic import BaseModel, ConfigDict

from .core.schemas import (
    FragmentRef,
    PipelineOptions,
    PipelineSpec,
    PromptConfig,
    StepConfig,
    SubPipelineSpec,
)


class ModelRef(BaseModel):
    """
    Reference to a shared model definition.

    Core fields:
        model: Model ID string
        system_prompt: Optional system prompt override

    Extra fields (optional, read by handlers):
        execution: Execution hints for handlers that support ChunkedModelExecutor
            - chunk_size: int (default 10)
            - max_concurrent: int | None
            - timeout_ms: int | None
            - sequential: bool (default False)
        prompt_override: str | None (alternative prompt_ref for this model)

    Invariant: ∀ extra field: handler reads via getattr(model_config, field, default)
    """

    model_config = ConfigDict(extra="allow")

    model: str
    system_prompt: str | None = None


class SharedModels(BaseModel):
    """Collection of shared model references."""

    models: dict[str, ModelRef]


class SharedPrompts(BaseModel):
    """Collection of shared prompt templates."""

    prompts: dict[str, object]  # Supports arbitrary nesting


__all__ = [
    "FragmentRef",
    "ModelRef",
    "PipelineOptions",
    "PipelineSpec",
    "PromptConfig",
    "SharedModels",
    "SharedPrompts",
    "StepConfig",
    "SubPipelineSpec",
]
