"""
Verification decision events.

Granular events emitted by the verify chain handler at each
internal decision point. These capture data that was previously
invisible in execution summaries (domain routing, authority
verdicts, per-model votes, tiebreaker decisions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import PipelineEvent


@dataclass(slots=True, kw_only=True)
class ClaimsExtracted(PipelineEvent):
    """Emitted after answer is decomposed into atomic claims."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    source_step: str = ""
    decompose_latency_ms: float = 0.0
    answer_sentences: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ClaimsClassified(PipelineEvent):
    """Emitted after claims are classified by domain (math, general, etc.)."""

    classifications: dict[str, str] = field(default_factory=dict)
    domain_counts: dict[str, int] = field(default_factory=dict)
    classify_latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class ClaimsContextualized(PipelineEvent):
    """Emitted after general claims are rewritten to be self-standing."""

    rewritten_count: int = 0
    skipped_count: int = 0
    contextualize_latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class DomainVerificationCompleted(PipelineEvent):
    """Emitted after domain-specific authority verification."""

    authority_verdicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    claims_routed_to_general: list[str] = field(default_factory=list)


@dataclass(slots=True, kw_only=True)
class ModelVerdictCast(PipelineEvent):
    """Emitted per verifier model with its verdicts on all claims."""

    verdicts: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class TiebreakerTriggered(PipelineEvent):
    """Emitted when borderline claims are sent to a tiebreaker model."""

    borderline_claim_ids: list[str] = field(default_factory=list)
    tiebreaker_model: str = ""
    total_claims: int = 0
    math_excluded: int = 0


@dataclass(slots=True, kw_only=True)
class ThresholdApplied(PipelineEvent):
    """Emitted after consensus threshold filters claims."""

    accepted_ids: list[str] = field(default_factory=list)
    rejected_ids: list[str] = field(default_factory=list)
    policy: str = ""
    math_policy: str = ""


@dataclass(slots=True, kw_only=True)
class CompoundClaimsDecomposed(PipelineEvent):
    """Emitted after compound general claims are split into atomic sub-claims."""

    decomposed_count: int = 0
    total_sub_claims: int = 0
    decompose_latency_ms: float = 0.0
    details: list[dict[str, Any]] = field(default_factory=list)
    # details: [{parent_id, parent_text, sub_claims: [{statement_id, text}]}]


@dataclass(slots=True, kw_only=True)
class VerificationComplete(PipelineEvent):
    """Final verification result with full claim data and vote matrix."""

    verified_facts: list[dict[str, Any]] = field(default_factory=list)
    rejected_claims: list[dict[str, Any]] = field(default_factory=list)
    verdicts_by_model: dict[str, dict[str, bool]] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    verifier_pool: list[str] = field(default_factory=list)
    originator: str = ""
    answer_sentences: list[str] = field(default_factory=list)
