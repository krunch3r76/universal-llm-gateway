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
    """Emitted per verifier model with its verdicts on all claims.

    Each verdict is {"v": bool, "r": str} — verdict + reasoning.
    """

    verdicts: dict[str, dict[str, str | bool]] = field(default_factory=dict)


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
class VetoPassCompleted(PipelineEvent):
    """Emitted after a dedicated veto step checks authority-accepted claims.

    The veto step re-verifies authority-accepted claims with a separate pool.
    Only unanimous FALSE from all veto models overrides the authority verdict.

    Each verdict in verdicts_by_model is {"v": bool, "r": str}.
    """

    authority_claims_checked: int = 0
    vetoed_ids: list[str] = field(default_factory=list)
    survived_ids: list[str] = field(default_factory=list)
    veto_pool: list[str] = field(default_factory=list)
    verdicts_by_model: dict[str, dict[str, dict[str, str | bool]]] = field(
        default_factory=dict
    )
    veto_policy: str = ""
    latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class SynergizeCompleted(PipelineEvent):
    """Emitted after fact sets are merged and deduplicated."""

    input_counts: dict[str, int] = field(default_factory=dict)
    output_count: int = 0
    duplicate_count: int = 0
    embedding_model: str = ""
    similarity_threshold: float = 0.0
    latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class FilterNegativesCompleted(PipelineEvent):
    """Emitted after universal negatives are classified and removed."""

    input_count: int = 0
    removed_count: int = 0
    removed_texts: list[str] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class EnrichReviewCompleted(PipelineEvent):
    """Emitted after enriched answer is reviewed for missing facts."""

    total_facts: int = 0
    missing_count: int = 0
    missing_indices: list[int] = field(default_factory=list)
    re_enriched: bool = False
    latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class DomainVetoCompleted(PipelineEvent):
    """Emitted after domain-specialist veto on non-unanimous accepted claims.

    The domain veto fires within a verify_chain step, after general consensus.
    A specialist model reviews accepted claims where the general pool was split.
    """

    domain: str = ""
    specialist_model: str = ""
    candidates_checked: int = 0
    vetoed_ids: list[str] = field(default_factory=list)
    survived_ids: list[str] = field(default_factory=list)
    verdicts: dict[str, bool] = field(default_factory=dict)
    latency_ms: float = 0.0


@dataclass(slots=True, kw_only=True)
class CombinePassagesCompleted(PipelineEvent):
    """Emitted after verified facts are synthesised into a combined answer.

    Payload:
        fact_count: Total verified facts sent to combine
        chunk_count: Number of synthesis chunks (1 = single call, N = chunked)
        cited_count: Unique fact indices cited at least once in the output
        uncited_indices: Fact indices with no citation in the output
        coverage_pct: cited_count / fact_count * 100
    """

    fact_count: int = 0
    chunk_count: int = 1
    cited_count: int = 0
    uncited_indices: list[int] = field(default_factory=list)
    coverage_pct: float = 0.0


@dataclass(slots=True, kw_only=True)
class OrganizeFactsCompleted(PipelineEvent):
    """Emitted after organize_facts generates and validates an outline."""

    total_facts: int = 0
    sections_created: int = 0
    facts_assigned: int = 0
    valid_json: bool = False


@dataclass(slots=True, kw_only=True)
class CoverageAuditCompleted(PipelineEvent):
    """Emitted after embedding-based fact coverage audit.

    Payload:
        total_facts: Verified facts checked against the answer
        covered_count: Facts with best-sentence similarity >= threshold
        uncovered_count: Facts below the threshold
        mean_score: Mean of per-fact best-sentence similarity scores
        coverage_pct: covered_count / total_facts * 100
        threshold: Similarity threshold used
    """

    total_facts: int = 0
    covered_count: int = 0
    uncovered_count: int = 0
    mean_score: float = 0.0
    coverage_pct: float = 0.0
    threshold: float = 0.0


@dataclass(slots=True, kw_only=True)
class VerificationComplete(PipelineEvent):
    """Final verification result with full claim data and vote matrix.

    Each verdict in verdicts_by_model is {"v": bool, "r": str}.
    """

    verified_facts: list[dict[str, Any]] = field(default_factory=list)
    rejected_claims: list[dict[str, Any]] = field(default_factory=list)
    verdicts_by_model: dict[str, dict[str, dict[str, str | bool]]] = field(
        default_factory=dict
    )
    stats: dict[str, Any] = field(default_factory=dict)
    verifier_pool: list[str] = field(default_factory=list)
    originator: str = ""
    answer_sentences: list[str] = field(default_factory=list)
