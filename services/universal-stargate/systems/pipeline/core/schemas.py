"""
Compatibility exports for pipeline core schemas.

Primary definitions now live in:
- pipeline-level: `pipeline_config.py`
- step-level: `step_config.py`
"""

from .pipeline_config import (
    FragmentRef,
    PipelineOptions,
    PipelineSpec,
    PromptConfig,
    SubPipelineSpec,
)
from .step_config import StepConfig
from .step_types import (
    CheckpointConfig,
    InputBinding,
    MapConfig,
    MapState,
    OutputBinding,
    SourceInput,
    StepInputs,
    StepOutput,
)

__all__ = [
    "CheckpointConfig",
    "FragmentRef",
    "InputBinding",
    "MapConfig",
    "MapState",
    "OutputBinding",
    "PipelineOptions",
    "PipelineSpec",
    "PromptConfig",
    "SourceInput",
    "StepConfig",
    "StepInputs",
    "StepOutput",
    "SubPipelineSpec",
]

