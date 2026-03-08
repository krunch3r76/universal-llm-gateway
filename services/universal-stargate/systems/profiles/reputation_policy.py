from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, kw_only=True)
class ReputationPolicy:
    error_weight: float = 0.5
    min_sample_floor: float = 5.0
    failure_rate_bad: float = 0.25
    # DEPRECATED: replaced by tok/s latency scoring
    latency_mix: float = 0.7
    # DEPRECATED: replaced by tok/s latency scoring
    latency_good_ms: float = 800.0
    # DEPRECATED: replaced by tok/s latency scoring
    latency_bad_ms: float = 6000.0
    # Ceiling for tok/s latency normalization: score = min(1.0, actual / reference)
    reference_toks_per_second: float = 60.0
    confidence_full_samples: float = 20.0
    prior_score: float = 0.60
    reliability_weight: float = 0.45
    latency_weight: float = 0.30
    quality_weight: float = 0.25
    ewma_half_life_seconds: float = 600.0
    min_switch_delta: float = 0.05
    switch_cooldown_s: int = 30


DEFAULT_REPUTATION_POLICY = ReputationPolicy()
