"""Unit tests for densify density triage + generate review envelope."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.densify_triage import (
    _AUTO_REVIEW_CHILD_WARNING,
    COMPOSER_DRAFT_SENTINEL,
    DENSIFY_DEFAULT_ON,
    REASONING_TRACE_SENTINEL,
    SEED_ONLY_SENTINEL,
    build_generate_review_envelope,
    validate_generate_density_intake,
)
from systems.frontier_consult.executor_resolution import derive_generate_review


def test_derive_generate_review_default_on() -> None:
    assert (
        derive_generate_review("judgment_required", auto_review_child=False)
        == "cross-family-reconcile:default-on"
    )
    assert derive_generate_review("trivial", auto_review_child=False) is None
    assert derive_generate_review(None, auto_review_child=False) is None


def test_derive_generate_review_child_suppresses() -> None:
    assert derive_generate_review("judgment_required", auto_review_child=True) is None


def test_recon_pending_density_is_valid() -> None:
    validate_generate_density_intake(
        request_id="r6",
        contract="light-bounded",
        density_triage="recon_pending",
        review_opt_out_reason_code=None,
        auto_review_child=False,
    )


def test_mechanical_density_is_valid() -> None:
    validate_generate_density_intake(
        request_id="r7",
        contract="implement",
        density_triage="mechanical",
        review_opt_out_reason_code=None,
        auto_review_child=False,
    )


def test_unknown_density_triage_422() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        validate_generate_density_intake(
            request_id="r1",
            contract="light-bounded",
            density_triage="bogus",
            review_opt_out_reason_code=None,
            auto_review_child=False,
        )
    assert exc.value.field == "density_triage"


def test_pure_mechanical_default_on_conflict() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        validate_generate_density_intake(
            request_id="r2",
            contract="pure-mechanical",
            density_triage="dispatch_surface",
            review_opt_out_reason_code=None,
            auto_review_child=False,
        )
    assert exc.value.code == "density_triage_mechanical_conflict"


def test_sdk_implement_default_on_conflict() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        validate_generate_density_intake(
            request_id="r3",
            contract="implement",
            density_triage="judgment_required",
            review_opt_out_reason_code=None,
            auto_review_child=False,
        )
    assert exc.value.code == "density_triage_implement_conflict"


def test_opt_out_non_default_on_422() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        validate_generate_density_intake(
            request_id="r4",
            contract="light-bounded",
            density_triage="trivial",
            review_opt_out_reason_code="routine_single_subsystem",
            auto_review_child=False,
        )
    assert exc.value.code == "review_opt_out_non_default_on"


def test_opt_out_child_lane_422() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        validate_generate_density_intake(
            request_id="r5",
            contract="light-bounded",
            density_triage="judgment_required",
            review_opt_out_reason_code="routine_single_subsystem",
            auto_review_child=True,
        )
    assert exc.value.code == "review_opt_out_child_lane"


def test_envelope_present_null_not_absent() -> None:
    env = build_generate_review_envelope(
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
    )
    assert "recommended_review" in env
    assert env["recommended_review"] is None


def test_envelope_default_on_advisory() -> None:
    env = build_generate_review_envelope(
        density_triage="cross_cutting",
        review_opt_out_reason_code=None,
        auto_review_child=False,
    )
    assert env["recommended_review"] == "cross-family-reconcile:default-on"


def test_envelope_valid_opt_out_preserves_advisory() -> None:
    env = build_generate_review_envelope(
        density_triage="admission_path",
        review_opt_out_reason_code="cost_exceeds_false_negative_risk",
        auto_review_child=False,
    )
    assert env["recommended_review"] == "cross-family-reconcile:default-on"
    assert env["review_opted_out"] is True
    assert env["auto_review_spawned"] is False


_ALL_DENSITY_VALUES = [
    None,
    "mechanical",
    "trivial",
    "recon_pending",
    "judgment_required",
    "cross_cutting",
    "dispatch_surface",
    "admission_path",
]


@pytest.mark.parametrize("density_triage", _ALL_DENSITY_VALUES)
def test_envelope_auto_review_child_wired_lane_no_warning(
    density_triage: str | None,
) -> None:
    env = build_generate_review_envelope(
        density_triage=density_triage,
        review_opt_out_reason_code=None,
        auto_review_child=True,
    )
    assert env["auto_review_child_requested"] is True
    assert env["auto_review_spawned"] is False
    assert "auto_review_child_warning" not in env
    if density_triage in DENSIFY_DEFAULT_ON:
        assert env["recommended_review"] == "cross-family-reconcile:default-on"
    else:
        assert env["recommended_review"] is None


@pytest.mark.parametrize("density_triage", _ALL_DENSITY_VALUES)
def test_envelope_auto_review_child_unwired_lane_emits_warning(
    density_triage: str | None,
) -> None:
    with patch(
        "systems.frontier_consult.generate_admission_context_store.is_generate_review_child_lane_wired",
        return_value=False,
    ):
        env = build_generate_review_envelope(
            density_triage=density_triage,
            review_opt_out_reason_code=None,
            auto_review_child=True,
        )
    assert env["auto_review_child_warning"] == _AUTO_REVIEW_CHILD_WARNING


@pytest.mark.parametrize("density_triage", _ALL_DENSITY_VALUES)
def test_envelope_auto_review_child_false_unchanged(
    density_triage: str | None,
) -> None:
    env = build_generate_review_envelope(
        density_triage=density_triage,
        review_opt_out_reason_code=None,
        auto_review_child=False,
    )
    assert "auto_review_child_requested" not in env
    assert "auto_review_child_warning" not in env
    assert "auto_review_spawned" not in env
    expected = (
        "cross-family-reconcile:default-on"
        if density_triage in DENSIFY_DEFAULT_ON
        else None
    )
    assert env["recommended_review"] == expected


def test_no_phase1_attestation_imports_in_densify_path() -> None:
    import subprocess

    proc = subprocess.run(
        [
            "rg",
            "-l",
            "ReviewAttestation|classify_risk_tier|implement_spec_hash",
            "services/universal-stargate/systems/frontier_consult/densify_triage.py",
            "services/universal-stargate/systems/frontier_consult/densify_candidate_ready.py",
            "services/universal-stargate/systems/frontier_consult/densify_review_reconcile.py",
            "services/universal-stargate/systems/frontier_consult/densify_routes.py",
            "services/universal-stargate/systems/frontier_consult/cursor_sdk_generate.py",
            "services/universal-stargate/systems/frontier_consult/api_role_generate.py",
        ],
        cwd="/mnt/torus/projects/universal-llm-gateway",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert proc.stdout.strip() == ""


def test_reviewer_sentinels_not_seed_only() -> None:
    from systems.frontier_consult.densify_candidate_ready import build_reviewer_prompt

    prompt = build_reviewer_prompt(
        staged_draft_body=f"{COMPOSER_DRAFT_SENTINEL}\n# draft",
        reasoning_trace_body=f"{REASONING_TRACE_SENTINEL}\n# trace",
    )
    assert COMPOSER_DRAFT_SENTINEL in prompt
    assert REASONING_TRACE_SENTINEL in prompt
    assert SEED_ONLY_SENTINEL not in prompt
