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


class CapabilityDispatchMaxOutput(BaseModel):
    """Mirror of libs ``CapabilityMaxOutput`` — verbatim native field, no renames."""

    model_config = ConfigDict(extra="forbid")

    default: int
    native_field: str
    ceiling: int | None = None
    floor: int | None = None
    over_ceiling: Literal["clamp", "reject"] = "clamp"


class CapabilityDispatchReasoning(BaseModel):
    """Mirror of libs ``CapabilityReasoningDispatch``."""

    model_config = ConfigDict(extra="forbid")

    native_field_path: str
    value_kind: Literal["effort_string", "token_budget", "adaptive"]
    accepted_values: list[str]
    default: str | None = None
    budget_map: dict[str, int] | None = None


class CapabilityDispatchKnobSpec(BaseModel):
    """Mirror of libs ``KnobSpec``.

    The libs ``OMIT`` sentinel (omit vs inject-explicit) is represented on the
    public facet by field absence; only present defaults are modelled here.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    accepted: list[str] | None = None
    default: Any | None = None


class CapabilityDispatchSpecializations(BaseModel):
    """Mirror of libs ``CapabilitySpecializations`` (typed-closed, G3)."""

    model_config = ConfigDict(extra="forbid")

    unsupported_values: list[tuple[str, str]] = Field(default_factory=list)
    behavioral_note: str | None = None
    evidence_uri: str | None = None


class CapabilityDispatchFacet(BaseModel):
    """Pydantic mirror of the libs-resident ``CapabilityDispatch`` (G3).

    Schema-of-record for per-model dispatch on ``ModelCapabilities``. G11
    carve-out (13137): this build populates the facet for LOCAL models only;
    CLOUD dispatch rows are NOT projected on ``/v1/models`` (tracked fast-follow).
    """

    model_config = ConfigDict(extra="forbid")

    api_surface: str
    max_output: CapabilityDispatchMaxOutput
    reasoning: CapabilityDispatchReasoning | None = None
    params: dict[str, CapabilityDispatchKnobSpec] = Field(default_factory=dict)
    specializations: CapabilityDispatchSpecializations | None = None

    @classmethod
    def from_dispatch(cls, dispatch: Any) -> "CapabilityDispatchFacet":
        """Build the facet from a libs ``CapabilityDispatch`` dataclass.

        Lazy structural copy — the gateway schema depends on libs (allowed
        direction), never the reverse. Used by the catalog projection path; the
        OMIT sentinel on knob defaults is dropped to field-absence here.
        """
        from llm_adapters.capability_dispatch.types import OMIT

        reasoning = None
        if dispatch.reasoning is not None:
            r = dispatch.reasoning
            reasoning = CapabilityDispatchReasoning(
                native_field_path=r.native_field_path,
                value_kind=r.value_kind,
                accepted_values=list(r.accepted_values),
                default=r.default,
                budget_map=dict(r.budget_map) if r.budget_map else None,
            )
        params = {
            name: CapabilityDispatchKnobSpec(
                name=spec.name,
                accepted=list(spec.accepted) if spec.accepted else None,
                default=None if spec.default is OMIT else spec.default,
            )
            for name, spec in dispatch.params.items()
        }
        specializations = None
        if dispatch.specializations is not None:
            s = dispatch.specializations
            specializations = CapabilityDispatchSpecializations(
                unsupported_values=[tuple(uv) for uv in s.unsupported_values],
                behavioral_note=s.behavioral_note,
                evidence_uri=s.evidence_uri,
            )
        mo = dispatch.max_output
        return cls(
            api_surface=dispatch.api_surface,
            max_output=CapabilityDispatchMaxOutput(
                default=mo.default,
                native_field=mo.native_field,
                ceiling=mo.ceiling,
                floor=mo.floor,
                over_ceiling=mo.over_ceiling,
            ),
            reasoning=reasoning,
            params=params,
            specializations=specializations,
        )


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
    dispatch: CapabilityDispatchFacet | None = Field(
        None,
        description=(
            "Per-model dispatch capability facet (mirror of libs "
            "CapabilityDispatch). G11 carve-out 13137: populated for LOCAL "
            "models only this build; cloud rows are a tracked /v1/models "
            "fast-follow."
        ),
    )


def capabilities_from_dict(data: dict[str, Any]) -> ModelCapabilities:
    """Parse capabilities from a dict (YAML/API)."""
    return ModelCapabilities.model_validate(data)


def default_capabilities_dict() -> dict[str, Any]:
    """Return default capabilities as dict for YAML/API."""
    return ModelCapabilities().model_dump()
