"""
Central ModelCapabilities schema for catalog metadata (v4).

Structured capability metadata replacing flat fields. All sub-models use
ConfigDict(extra="forbid") for YAML typo detection.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CapabilityModalities(BaseModel):
    """Input/output modalities (text, vision, etc.)."""

    model_config = ConfigDict(extra="forbid")

    input: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Input modalities (e.g. text, vision)",
    )
    output: list[str] = Field(
        default_factory=lambda: ["text"],
        description="Output modalities",
    )
    vision_architecture: str | None = Field(
        None, description="Vision architecture (e.g. qwen2_vl)"
    )


class CapabilityInteraction(BaseModel):
    """Interaction capabilities."""

    model_config = ConfigDict(extra="forbid")

    chat_template: bool = Field(False, description="Model has built-in chat template")


class CapabilityReasoning(BaseModel):
    """Reasoning capabilities."""

    model_config = ConfigDict(extra="forbid")

    supports_thinking: bool = Field(
        False,
        description="Supports extended thinking / chain-of-thought via chat template",
    )


class CapabilityLimits(BaseModel):
    """Model limits."""

    model_config = ConfigDict(extra="forbid")

    max_context_length: int | None = Field(
        None, description="Training / max context length"
    )


class CapabilityProvenance(BaseModel):
    """Provenance metadata."""

    model_config = ConfigDict(extra="forbid")

    license: str | None = Field(None, description="Model license")


class CapabilitySpecialization(BaseModel):
    """Optional specialization metadata (extensible)."""

    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(default_factory=list, description="Specialization tags")


class ModelCapabilities(BaseModel):
    """Structured model capability metadata (catalog v4)."""

    model_config = ConfigDict(extra="forbid")

    input_schema: Literal["prompt", "messages"] = Field(
        default="messages",
        description="Input format: prompt or messages",
    )
    modalities: CapabilityModalities = Field(
        default_factory=CapabilityModalities,
        description="Input/output modalities",
    )
    interaction: CapabilityInteraction = Field(
        default_factory=CapabilityInteraction,
        description="Interaction capabilities",
    )
    reasoning: CapabilityReasoning = Field(
        default_factory=CapabilityReasoning,
        description="Reasoning capabilities",
    )
    limits: CapabilityLimits = Field(
        default_factory=CapabilityLimits,
        description="Model limits",
    )
    provenance: CapabilityProvenance = Field(
        default_factory=CapabilityProvenance,
        description="Provenance metadata",
    )
    specialization: CapabilitySpecialization | None = Field(
        None, description="Optional specialization"
    )


def capabilities_from_dict(data: dict[str, Any]) -> ModelCapabilities:
    """Parse capabilities from a dict (YAML/API)."""
    return ModelCapabilities.model_validate(data)


def default_capabilities_dict() -> dict[str, Any]:
    """Return default capabilities as dict for YAML/API."""
    return ModelCapabilities().model_dump()
