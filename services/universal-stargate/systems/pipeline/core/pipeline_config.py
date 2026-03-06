"""
Pipeline-level schema definitions.

Contains high-level pipeline configuration classes and prompt configuration.
Step-level schemas live in `step_config.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .step_config import StepConfig


class PipelineOptions(BaseModel):
    """
    Generic pipeline options.

    Common fields are explicit; domain-specific fields in extras.

    Note: Pipeline responses are strictly OpenAI-compliant. No custom
    fields (like pipeline.messages) are included. Clients maintain
    conversation history as with standard OpenAI API.
    """

    model_config = ConfigDict(extra="allow")

    # Common options (domain-agnostic)
    include_alternates: bool = False
    include_step_stats: bool = False  # Per-step token breakdown in response
    # include_user_message removed - strict OpenAI compliance
    timeout_seconds: int = 60
    max_tokens: int | None = None
    skip_token_counting: bool = False
    # Profile control — pipelines manage their own generation parameters,
    # so model-assigned profiles (e.g. "creative" on qwen2-5) are suppressed by default.
    # Set disable_profile=False to allow profile assignment, or set profile
    # to a specific profile name to apply it to all steps.
    disable_profile: bool = True
    profile: str | None = None
    save_execution_summary: bool = False  # Write detailed execution log to disk
    summary_format: str = (
        "markdown"  # Format: "markdown" (default), "yaml", "json", or "all"
    )

    def get(self, key: str, default: Any = None) -> Any:
        """Get option by key (explicit or extra)."""
        if hasattr(self, key):
            return getattr(self, key)
        return (self.model_extra or {}).get(key, default)

    def to_context_dict(self) -> dict[str, Any]:
        """
        Convert to dict for prompt context.

        Handles schema definitions (dicts with 'type', 'description', 'default')
        by extracting the 'default' value instead of returning the schema dict.
        """
        result = self.model_dump()
        result.update(self.model_extra or {})

        # Extract defaults from schema definitions
        # Schema format: {"type": "...", "description": "...", "default": value}
        for key, value in list(result.items()):
            if isinstance(value, dict) and "default" in value and "type" in value:
                # This is a schema definition, extract the default value
                result[key] = value["default"]

        return result


class FragmentRef(BaseModel):
    """Reference to a reusable pipeline fragment."""

    use: str
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    as_prefix: str | None = None


class PipelineSpec(BaseModel):
    """
    Generic pipeline specification.

    The `type` field determines which domain handles execution.
    """

    model_config = ConfigDict(extra="allow")

    _VALID_OUTPUT_FORMATS: ClassVar[frozenset[str]] = frozenset({"json_array"})

    id: str
    version: str
    type: str  # Open - "translation", "code_review", "multimodal", etc.
    options: PipelineOptions = Field(default_factory=PipelineOptions)
    steps: list[StepConfig]
    output: str
    output_format: str | None = None

    # Fragment definitions within this pipeline
    fragments: dict[str, list[dict]] | None = None

    # Checkpoint (Phase 3)
    checkpoint: dict[str, Any] | None = None  # Checkpoint configuration

    # Which search path this pipeline was loaded from (e.g. "pipelines.local")
    # Used for search-path-scoped model resolution (isolation semantics)
    source_search_path: str = ""

    # Which variant directory this pipeline was loaded from (e.g. "v6.0")
    # Used for variant-scoped handler dispatch (isolation semantics)
    source_variant: str = ""

    @field_validator("output_format")
    @classmethod
    def validate_output_format(cls, v: str | None) -> str | None:
        if v is not None and v not in cls._VALID_OUTPUT_FORMATS:
            raise ValueError(
                f"Unknown output_format {v!r}. "
                f"Valid values: {', '.join(sorted(cls._VALID_OUTPUT_FORMATS))}"
            )
        return v

    @property
    def domain(self) -> str:
        """Alias for type - clarifies domain routing."""
        return self.type


class SubPipelineSpec(BaseModel):
    """Sub-pipeline specification loaded from a separate YAML file.

    Lighter than PipelineSpec: declares an inputs/outputs interface
    so a parent pipeline step can bind its data flow declaratively.

    Invariant: ∀ step ∈ steps: internal bindings use step names
    local to this sub-pipeline (no parent awareness).
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    inputs: list[str]
    steps: list[StepConfig]
    output: str


@dataclass
class PromptConfig:
    """
    Structured prompt configuration.

    Replaces flat string prompts with self-contained configuration
    that includes all context needed for model invocation.

    Invariants:
    - ∀ p: PromptConfig, p.template ≠ ∅
    - system_prompt may be None
    - json_schema belongs in step generation_parameters.response_format, not here

    Generation parameters and response_format REMOVED - step-config-only.

    Attributes:
        name: Prompt identifier (last part of prompt_ref)
        description: Human-readable description
        system_prompt: Optional system message for model
        template: User prompt template with {placeholders}
    """

    name: str
    description: str = ""
    system_prompt: str | None = None
    template: str = ""

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        if not self.template or not self.template.strip():
            raise ValueError(
                f"PromptConfig '{self.name}' has empty template. "
                f"Template is required for all prompts."
            )

