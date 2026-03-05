"""Intelligence profile schema — per-model quality and suitability metadata.

Captures *how well* and *for what* a model performs, complementing:
- ModelCapabilities (intrinsic inference facts)
- model_profiles.yaml (generation parameters)
- consult-roles.yaml (role requirements)

Score vocabulary: strong | good | neutral | weak | exclude
All dimensions use the same enum for uniform comparison and querying.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Score = Literal["strong", "good", "neutral", "weak", "exclude"]
EvidenceSource = Literal["benchmark", "curated", "telemetry", "pipeline"]
Suitability = Literal["preferred", "neutral", "avoid", "exclude"]
Scope = Literal["observed", "policy", "benchmark", "pipeline"]

SCORE_ORDER: dict[str, int] = {
    "strong": 4,
    "good": 3,
    "neutral": 2,
    "weak": 1,
    "exclude": 0,
}


def score_gte(actual: Score | None, threshold: Score) -> bool:
    """Return True if actual score meets or exceeds threshold."""
    if actual is None:
        return False
    return SCORE_ORDER.get(actual, 0) >= SCORE_ORDER.get(threshold, 0)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: EvidenceSource
    detail: str | None = None


class DomainScore(BaseModel):
    """Score for a knowledge domain or task, with optional evidence."""

    model_config = ConfigDict(extra="allow")

    score: Score | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class GenerationQuality(BaseModel):
    """Multi-dimensional quality assessment for generation tasks."""

    model_config = ConfigDict(extra="allow")

    technical_quality: Score | None = None
    semantic_alignment: Score | None = None
    style_adherence: Score | None = None
    compositional_accuracy: Score | None = None
    naturalness: Score | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class StyleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terse: bool | None = None
    creative: bool | None = None
    formal: bool | None = None
    compliant: bool | None = None
    markdown_preference: Score | None = None
    persona_tags: list[str] = Field(default_factory=list)


class CrossModal(BaseModel):
    """Cross-modality integration quality (multimodal models only)."""

    model_config = ConfigDict(extra="allow")

    grounding: Score | None = None
    modality_weighting: Score | None = None
    context_retention_mixed: Score | None = None
    cross_modal_consistency: Score | None = None
    instruction_following_cross: Score | None = None


class RoleSuitabilityEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suitability: Suitability
    scope: Scope = "observed"
    reason: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class LanguageCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strong: list[str] = Field(default_factory=list)
    good: list[str] = Field(default_factory=list)
    weak: list[str] = Field(default_factory=list)


class VariantEntry(BaseModel):
    """Maps a concrete model ID to a source (local/cloud)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: Literal["local", "cloud"]


class IntelligenceProfile(BaseModel):
    """Per-model intelligence profile.

    extra="allow" on root so new top-level facets need no schema migration.
    """

    model_config = ConfigDict(extra="allow")

    basename: str
    full_model_id: str | None = None
    variants: list[VariantEntry] = Field(default_factory=list)

    domains: dict[str, DomainScore] = Field(default_factory=dict)
    tasks: dict[str, DomainScore | GenerationQuality] = Field(default_factory=dict)
    style: StyleProfile = Field(default_factory=StyleProfile)
    languages: LanguageCoverage = Field(default_factory=LanguageCoverage)
    cross_modal: CrossModal | None = None
    role_suitability: dict[str, RoleSuitabilityEntry] = Field(default_factory=dict)

    recommended_params: dict[str, Any] | None = None

    hallucination_rate: Score | None = None
    reasoning_depth: Score | None = None
    tool_usage_skill: Score | None = None
    citation_quality: Score | None = None
    alignment_risk: Literal["safe", "middling", "unaligned"] | None = None
    toxicity: Score | None = None
    latency_bucket: Literal["fast", "medium", "slow"] | None = None
    cost_bucket: Literal["cheap", "medium", "expensive"] | None = None
