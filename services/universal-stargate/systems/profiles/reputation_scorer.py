from __future__ import annotations

from dataclasses import dataclass

from .reputation_policy import DEFAULT_REPUTATION_POLICY, ReputationPolicy
from .reputation_store import ReputationRecord


@dataclass(slots=True, kw_only=True)
class ReputationComponents:
    reliability: float
    latency: float
    quality: float
    confidence: float
    prior: float
    observed: float


@dataclass(slots=True, kw_only=True)
class ReputationScore:
    model_id: str
    final_score: float
    components: ReputationComponents


def score_record(
    *,
    model_id: str,
    record: ReputationRecord | None,
    policy: ReputationPolicy = DEFAULT_REPUTATION_POLICY,
) -> ReputationScore:
    if record is None:
        components = ReputationComponents(
            reliability=1.0,
            latency=0.5,  # neutral until tok/s observed
            quality=policy.prior_score,
            confidence=0.0,
            prior=policy.prior_score,
            observed=policy.prior_score,
        )
        return ReputationScore(
            model_id=model_id, final_score=policy.prior_score, components=components
        )

    reliability = max(
        0.0,
        1.0
        - (
            policy.error_weight * record.error_ewma
            + (1.0 - policy.error_weight) * record.timeout_ewma
        ),
    )
    # Normalize tok/s against reference ceiling; neutral 0.5 when not yet observed
    if record.toks_per_second_ewma is not None:
        latency = min(
            1.0, record.toks_per_second_ewma / policy.reference_toks_per_second
        )
    else:
        latency = 0.5
    quality = max(0.0, min(1.0, record.quality_ewma))
    observed = (
        policy.reliability_weight * reliability
        + policy.latency_weight * latency
        + policy.quality_weight * quality
    )
    confidence = max(
        0.0,
        min(1.0, record.request_ewma / policy.confidence_full_samples),
    )
    final = confidence * observed + (1.0 - confidence) * policy.prior_score
    components = ReputationComponents(
        reliability=reliability,
        latency=latency,
        quality=quality,
        confidence=confidence,
        prior=policy.prior_score,
        observed=observed,
    )
    return ReputationScore(model_id=model_id, final_score=final, components=components)
