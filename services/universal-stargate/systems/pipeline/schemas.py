"""
Pipeline schemas - exports from core.

Re-exports the core pipeline Pydantic models (``PipelineSpec``,
``StepConfig``, ``SubPipelineSpec``, ``PromptConfig``, ``FragmentRef``,
``PipelineOptions``) and defines the shared-config models ``ModelRef``,
``SharedModels`` and ``SharedPrompts`` that ``loader.PipelineConfigLoader``
builds from YAML. Re-exported again by the ``systems.pipeline`` package.
"""

from typing import Any

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
        profile: Inference profile name to apply for this model (e.g.
            "qwen3-5-instruct", "qwen3-5-instruct-reasoning"). Inserts between
            step-level and pipeline-level profile in the resolution hierarchy.
            Setting this implicitly enables profile application — no need to
            set disable_profile: false separately, unless the step or pipeline
            explicitly disables it.

    Extra fields (optional, read by handlers):
        execution: Execution hints for handlers that support ChunkedModelExecutor
            - chunk_size: int (default 1)
            - max_concurrent: int | None
            - timeout_ms: int | None
            - sequential: bool (default False)
        prompt_override: str | None (alternative prompt_ref for this model)

    Invariant: ∀ extra field: handler reads via getattr(model_config, field, default)
    Invariant: ¬system_prompt — system prompts belong on the step
        (StepConfig.system_prompt)
               or in the prompt definition (prompts.yaml). ModelRef is model routing,
                   not
               prompt engineering.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    profile: str | None = None


class SharedModels(BaseModel):
    """Collection of shared model references.

    Parsed by ``PipelineConfigLoader`` from ``pipeline_models.yaml``; maps a
    shared model alias to its ``ModelRef`` (model ID plus optional profile and
    extra handler execution hints) so steps can reference models by name.
    """

    models: dict[str, ModelRef]


class SharedPrompts(BaseModel):
    """Collection of shared prompt templates.

    Built by ``PipelineConfigLoader`` by merging every
    ``pipeline_prompts*.yaml``; ``prompts`` is an arbitrarily nested dict where
    suffixed files contribute a top-level namespace (e.g. ``transformation``).
    """

    prompts: dict[str, Any]  # Supports arbitrary nesting


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
