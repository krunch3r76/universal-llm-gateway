"""
Verdict-classed VRAM admission for routing resource checks.

Classifies admit vs transient / margin / structural insufficient using capped
headroom and attainable ceiling — shared by resource_checks and reclaim paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AdmissionVerdict(StrEnum):
    """VRAM capacity verdict for one gateway/model pair (admit vs insufficient)."""

    ADMIT = "admit"
    INSUFFICIENT_TRANSIENT = "insufficient_transient"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    INSUFFICIENT_STRUCTURAL = "insufficient_structural"


@dataclass(frozen=True, slots=True)
class AdmissionEvaluation:
    """Structured admission decision with observability payload fields."""

    verdict: AdmissionVerdict
    needed_mb: int
    footprint_est_mb: int
    margin_mb: int
    attainable_mb: int
    reserved_mb: int
    effective_free_mb: int
    vram_margin_pct: int
    vram_headroom_floor_mb: int
    vram_margin_cap_mb: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "verdict_class": self.verdict.value,
            "needed_mb": self.needed_mb,
            "footprint_est_mb": self.footprint_est_mb,
            "margin_mb": self.margin_mb,
            "attainable_mb": self.attainable_mb,
            "reserved_mb": self.reserved_mb,
            "vram_free_effective": self.effective_free_mb,
            "vram_margin_pct": self.vram_margin_pct,
        }

    @property
    def is_permanent(self) -> bool:
        return self.verdict == AdmissionVerdict.INSUFFICIENT_STRUCTURAL

    @property
    def is_retryable(self) -> bool:
        return not self.is_permanent


_MIN_NONRECLAIMABLE_VRAM_MB = 512
_NONRECLAIMABLE_VRAM_RATIO = 0.02
_DEFAULT_VRAM_MARGIN_CAP_MB = 4096


def _clamp(value: int, floor_mb: int, cap_mb: int) -> int:
    if cap_mb <= 0:
        return max(value, floor_mb)
    return min(max(value, floor_mb), cap_mb)


def _nonreclaimable_overhead_mb(vram_total_mb: int) -> int:
    if vram_total_mb <= 0:
        return _MIN_NONRECLAIMABLE_VRAM_MB
    return max(
        _MIN_NONRECLAIMABLE_VRAM_MB,
        int(vram_total_mb * _NONRECLAIMABLE_VRAM_RATIO),
    )


def _attainable_ceiling_mb(vram_total_mb: int, vram_free_mb: int) -> int:
    if vram_total_mb > 0:
        return max(0, vram_total_mb - _nonreclaimable_overhead_mb(vram_total_mb))
    return max(0, vram_free_mb)


def compute_capped_margin_mb(
    footprint_est_mb: int,
    *,
    vram_margin_pct: int,
    vram_headroom_floor_mb: int,
    vram_margin_cap_mb: int = _DEFAULT_VRAM_MARGIN_CAP_MB,
) -> int:
    """Return clamp(pct×footprint, floor_mb, abs_cap_mb) as the headroom margin."""
    pct_component = int(footprint_est_mb * (vram_margin_pct / 100))
    return _clamp(pct_component, vram_headroom_floor_mb, vram_margin_cap_mb)


def evaluate_vram_admission(
    *,
    footprint_est_mb: int,
    vram_free_mb: int,
    vram_total_mb: int,
    reserved_mb: int,
    resource_margins: dict[str, float | int] | None = None,
) -> AdmissionEvaluation:
    """Classify VRAM admit/insufficient using capped headroom and attainable ceiling."""
    margins = resource_margins or {}
    vram_margin_pct = int(margins.get("vram_margin_pct", 5))
    vram_headroom_floor_mb = int(margins.get("vram_headroom_mb", 2048))
    vram_margin_cap_mb = int(
        margins.get("vram_margin_cap_mb", _DEFAULT_VRAM_MARGIN_CAP_MB)
    )

    margin_mb = compute_capped_margin_mb(
        footprint_est_mb,
        vram_margin_pct=vram_margin_pct,
        vram_headroom_floor_mb=vram_headroom_floor_mb,
        vram_margin_cap_mb=vram_margin_cap_mb,
    )
    needed_mb = footprint_est_mb + margin_mb if footprint_est_mb > 0 else 0
    attainable_mb = _attainable_ceiling_mb(vram_total_mb, vram_free_mb)
    effective_free_mb = vram_free_mb - reserved_mb

    if footprint_est_mb <= 0 or effective_free_mb >= needed_mb:
        verdict = AdmissionVerdict.ADMIT
    elif needed_mb > attainable_mb:
        verdict = AdmissionVerdict.INSUFFICIENT_STRUCTURAL
    elif reserved_mb > 0:
        verdict = AdmissionVerdict.INSUFFICIENT_TRANSIENT
    else:
        verdict = AdmissionVerdict.INSUFFICIENT_MARGIN

    return AdmissionEvaluation(
        verdict=verdict,
        needed_mb=needed_mb,
        footprint_est_mb=footprint_est_mb,
        margin_mb=margin_mb,
        attainable_mb=attainable_mb,
        reserved_mb=reserved_mb,
        effective_free_mb=effective_free_mb,
        vram_margin_pct=vram_margin_pct,
        vram_headroom_floor_mb=vram_headroom_floor_mb,
        vram_margin_cap_mb=vram_margin_cap_mb,
    )
