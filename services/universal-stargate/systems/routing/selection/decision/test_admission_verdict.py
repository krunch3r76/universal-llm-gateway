"""Unit tests for verdict-classed VRAM admission (replay 52b7e831 fixtures)."""

from systems.routing.selection.decision.admission_verdict import (
    AdmissionVerdict,
    compute_non_evictable_ram_reserve_mb,
    evaluate_ram_reclaim,
    evaluate_vram_admission,
)

_FIXTURE_MARGINS = {
    "vram_margin_pct": 2,
    "vram_headroom_mb": 0,
    "vram_margin_cap_mb": 99999,
}


def test_reserve_15134_is_transient_not_structural() -> None:
    """Large loading reserve must classify as transient, not structural."""
    evaluation = evaluate_vram_admission(
        footprint_est_mb=31308,
        vram_free_mb=31601,
        vram_total_mb=32607,
        reserved_mb=15134,
        resource_margins=_FIXTURE_MARGINS,
    )

    assert evaluation.verdict == AdmissionVerdict.INSUFFICIENT_TRANSIENT
    assert evaluation.verdict != AdmissionVerdict.INSUFFICIENT_STRUCTURAL
    assert evaluation.needed_mb == 31934
    assert evaluation.footprint_est_mb == 31308
    assert evaluation.margin_mb == 626
    assert evaluation.attainable_mb == 31955
    assert evaluation.reserved_mb == 15134
    payload = evaluation.to_payload()
    for field in (
        "verdict_class",
        "needed_mb",
        "footprint_est_mb",
        "margin_mb",
        "attainable_mb",
        "reserved_mb",
    ):
        assert field in payload


def test_reserve_zero_is_margin_or_admit_never_permanent() -> None:
    """Zero reserve with knife-edge free VRAM is margin shortfall, not permanent."""
    evaluation = evaluate_vram_admission(
        footprint_est_mb=31308,
        vram_free_mb=31601,
        vram_total_mb=32607,
        reserved_mb=0,
        resource_margins=_FIXTURE_MARGINS,
    )

    assert evaluation.verdict == AdmissionVerdict.INSUFFICIENT_MARGIN
    assert evaluation.verdict != AdmissionVerdict.INSUFFICIENT_STRUCTURAL


def test_required_min_above_attainable_ceiling_is_structural() -> None:
    """Footprint above attainable ceiling is permanent/structural infeasibility."""
    evaluation = evaluate_vram_admission(
        footprint_est_mb=40000,
        vram_free_mb=31601,
        vram_total_mb=32607,
        reserved_mb=0,
        resource_margins=_FIXTURE_MARGINS,
    )

    assert evaluation.verdict == AdmissionVerdict.INSUFFICIENT_STRUCTURAL
    assert evaluation.is_permanent
    assert evaluation.needed_mb > evaluation.attainable_mb
    assert evaluation.to_payload()["verdict_class"] == "insufficient_structural"


def test_ram_full_evict_uses_hardware_reclaim_correction() -> None:
    """Full eviction may reclaim observed RAM beyond the stale catalog total."""
    evaluation = evaluate_ram_reclaim(
        needed_mb=34_129,
        catalog_freeable_mb=3_190,
        ram_free_mb=5_000,
        ram_total_mb=64_000,
        loading_reservation_mb=0,
        full_evict=True,
    )

    assert evaluation.verdict is AdmissionVerdict.ADMIT
    assert evaluation.correction_applied is True
    assert evaluation.correction_basis == "corrected"
    assert evaluation.freeable_mb == 52_600
    assert evaluation.to_payload()["ram_verdict_class"] == "admit"


def test_ram_reclaim_does_not_invent_hardware_capacity_without_full_evict() -> None:
    """Partial eviction and unknown totals remain catalog-only by invariant."""
    partial = evaluate_ram_reclaim(
        needed_mb=10_000,
        catalog_freeable_mb=3_190,
        ram_free_mb=5_000,
        ram_total_mb=64_000,
        loading_reservation_mb=0,
        full_evict=False,
    )
    unknown_total = evaluate_ram_reclaim(
        needed_mb=10_000,
        catalog_freeable_mb=3_190,
        ram_free_mb=5_000,
        ram_total_mb=0,
        loading_reservation_mb=0,
        full_evict=True,
    )

    assert partial.correction_applied is False
    assert partial.freeable_mb == 3_190
    assert unknown_total.correction_applied is False
    assert unknown_total.freeable_mb == 3_190


def test_ram_reserve_defaults_are_floor_or_percent_of_hardware() -> None:
    """RAM reserve preserves the operator-bound 4096 MB / 10% policy."""
    assert compute_non_evictable_ram_reserve_mb(16_000) == 4_096
    assert compute_non_evictable_ram_reserve_mb(64_000) == 6_400
