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


@dataclass(frozen=True, slots=True)
class RamAdmissionEvaluation:
    """Structured RAM reclaim decision with namespaced diagnostic fields."""

    verdict: AdmissionVerdict
    needed_mb: int
    attainable_mb: int
    freeable_mb: int
    deficit_mb: int
    reserve_mb: int
    correction_applied: bool
    correction_basis: str

    def to_payload(self) -> dict[str, Any]:
        """Return RAM-prefixed fields without overwriting VRAM diagnostics."""
        return {
            "ram_verdict_class": self.verdict.value,
            "ram_attainable_mb": self.attainable_mb,
            "ram_freeable_mb": self.freeable_mb,
            "ram_deficit_mb": self.deficit_mb,
            "ram_non_evictable_reserve_mb": self.reserve_mb,
            "ram_correction_applied": self.correction_applied,
            "ram_correction_basis": self.correction_basis,
        }


_MIN_NONRECLAIMABLE_VRAM_MB = 512
_NONRECLAIMABLE_VRAM_RATIO = 0.02
_DEFAULT_VRAM_MARGIN_CAP_MB = 4096
_DEFAULT_RAM_NON_EVICTABLE_RESERVE_MB = 4096
_DEFAULT_RAM_NON_EVICTABLE_RESERVE_PCT = 0.10


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


def compute_non_evictable_ram_reserve_mb(
    ram_total_mb: int,
    resource_margins: dict[str, float | int] | None = None,
) -> int:
    """Return RAM reserve that eviction must not count as model-reclaimable."""
    margins = resource_margins or {}
    floor_mb = int(
        margins.get(
            "ram_non_evictable_reserve_floor_mb",
            _DEFAULT_RAM_NON_EVICTABLE_RESERVE_MB,
        )
    )
    reserve_pct = float(
        margins.get(
            "ram_non_evictable_reserve_pct",
            _DEFAULT_RAM_NON_EVICTABLE_RESERVE_PCT,
        )
    )
    if ram_total_mb <= 0:
        return floor_mb
    return max(floor_mb, int(ram_total_mb * reserve_pct))


def evaluate_ram_reclaim(
    *,
    needed_mb: int,
    catalog_freeable_mb: int,
    ram_free_mb: int,
    ram_total_mb: int,
    loading_reservation_mb: int,
    full_evict: bool,
    resource_margins: dict[str, float | int] | None = None,
) -> RamAdmissionEvaluation:
    """Classify RAM reclaimability using a full-evict hardware ceiling."""
    reserve_mb = compute_non_evictable_ram_reserve_mb(ram_total_mb, resource_margins)
    effective_free_mb = max(0, ram_free_mb - loading_reservation_mb)
    correction_applied = False
    correction_basis = "catalog"
    freeable_mb = max(0, catalog_freeable_mb)

    if ram_total_mb > 0 and full_evict:
        used_mb = max(0, ram_total_mb - ram_free_mb)
        hardware_freeable_mb = max(0, used_mb - reserve_mb)
        if hardware_freeable_mb > freeable_mb:
            freeable_mb = hardware_freeable_mb
            correction_applied = True
            correction_basis = "corrected"
        attainable_mb = max(0, ram_total_mb - reserve_mb - loading_reservation_mb)
    else:
        attainable_mb = effective_free_mb + freeable_mb

    available_mb = effective_free_mb + freeable_mb
    deficit_mb = max(0, needed_mb - min(available_mb, attainable_mb))
    if needed_mb <= 0 or available_mb >= needed_mb:
        verdict = AdmissionVerdict.ADMIT
    elif needed_mb > attainable_mb:
        verdict = AdmissionVerdict.INSUFFICIENT_STRUCTURAL
    elif loading_reservation_mb > 0:
        verdict = AdmissionVerdict.INSUFFICIENT_TRANSIENT
    else:
        verdict = AdmissionVerdict.INSUFFICIENT_MARGIN

    return RamAdmissionEvaluation(
        verdict=verdict,
        needed_mb=needed_mb,
        attainable_mb=attainable_mb,
        freeable_mb=freeable_mb,
        deficit_mb=deficit_mb,
        reserve_mb=reserve_mb,
        correction_applied=correction_applied,
        correction_basis=correction_basis,
    )


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
