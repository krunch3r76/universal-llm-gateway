"""Model requirements schema for declarative pipeline model selection.

Pipeline steps declare *what task* they need via a structured
model_requirements block. The profile system resolves these requirements
into a ranked list of concrete, routable model IDs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LATENCY_ORDER: dict[str, int] = {"fast": 0, "medium": 1, "slow": 2}


class ProviderDiversity(BaseModel):
    """Constraint ensuring models come from different providers."""

    model_config = ConfigDict(extra="forbid")

    min_unique: int = Field(default=1, ge=1)


class CostBudget(BaseModel):
    """Cost constraint for model selection ($/M completion tokens)."""

    model_config = ConfigDict(extra="forbid")

    max_per_model: float | None = None


class ModelRequirements(BaseModel):
    """Declarative model selection requirements for pipeline steps.

    Resolved to a ranked list of model IDs at step init time.
    For map steps, populates the model_pool. For single-model steps,
    the top-ranked result is used.

    Precedence (highest to lowest):
      1. optionsNs.model_ref (pipeline option override)
      2. model_ref on step (direct model ID or cloud: prefix)
      3. model_requirements (this, declarative)
    """

    model_config = ConfigDict(extra="forbid")

    task: str = Field(
        description="Task name matching profile tasks key, e.g. 'code_review'"
    )
    min_score: Literal["strong", "good", "neutral"] | None = Field(
        default=None,
        description="Minimum acceptable score for the task",
    )
    count: int = Field(default=1, ge=1, le=20)
    min_context: int | None = Field(
        default=None,
        ge=0,
        description="Minimum context window in tokens",
    )
    require_tools: bool | None = Field(
        default=None,
        description="Require function-calling / structured output support",
    )
    source: Literal["cloud", "local", "any"] = Field(
        default="cloud",
        description="Preferred model source",
    )
    provider_diversity: ProviderDiversity | None = None
    cost_budget: CostBudget | None = None
    max_latency_bucket: Literal["fast", "medium", "slow"] | None = Field(
        default=None,
        description=(
            "Reject models with a known latency_bucket strictly slower than this. "
            "Models with latency_bucket=None (unknown) are never rejected. "
            "Ordering: fast < medium < slow."
        ),
    )
    large_payload_latency_bucket: Literal["fast", "medium", "slow"] | None = Field(
        default=None,
        description=(
            "max_latency_bucket to enforce when estimated_source_tokens exceeds "
            "large_payload_threshold_tokens. Stricter than max_latency_bucket wins."
        ),
    )
    large_payload_threshold_tokens: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Source-token count above which large_payload_latency_bucket is activated."
        ),
    )
