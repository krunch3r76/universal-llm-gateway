"""Unit tests for verdict-classed VRAM admission (replay 52b7e831 fixtures)."""

from systems.routing.selection.decision.admission_verdict import (
    AdmissionVerdict,
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
