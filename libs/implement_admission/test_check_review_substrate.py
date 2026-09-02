"""Tests for check/review dual-substrate admission (dispatch-dual-substrate-option)."""

from __future__ import annotations

from implement_admission.check_review_substrate import (
    CheckReviewAdmissionReject,
    evaluate_check_review_admission,
    load_check_review_default_model,
    resolve_check_review_model,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import verify_workflow_registry_conformance


def test_omit_model_resolves_reviewer_to_terra() -> None:
    default_model = load_check_review_default_model(load_route_policy())
    resolution = resolve_check_review_model("reviewer", None)
    assert resolution.resolved_model == default_model
    # decision:code-review-panel-cursor-substrate — omit model lands on cursor-sdk, not API.
    assert resolution.substrate == "cursor-sdk"


def test_cursor_luna_resolves_sdk_substrate() -> None:
    resolution = resolve_check_review_model(
        "cursor-sdk", "cursor/gpt-5.6-luna"
    )
    assert resolution.substrate == "cursor-sdk"
    assert resolution.delivery_from_role == "reviewer"


def test_api_reviewer_with_cursor_model_rejects_sdk_substrate_required() -> None:
    verdict = evaluate_check_review_admission(
        "reviewer",
        "cursor/gpt-5.6-luna",
        api_role_with_cursor_on_api_profile=True,
    )
    assert isinstance(verdict, CheckReviewAdmissionReject)
    assert verdict.code == "sdk_substrate_required"


def test_judgment_role_with_gemini_flash_rejects_profile_mismatch() -> None:
    verdict = evaluate_check_review_admission(
        "skeptic",
        "cursor/gemini-3.5-flash",
        api_role_with_cursor_on_api_profile=True,
    )
    assert isinstance(verdict, CheckReviewAdmissionReject)
    assert verdict.code == "profile_mismatch"


def test_synthesizer_with_cursor_rejects_substrate_unsupported() -> None:
    verdict = evaluate_check_review_admission(
        "synthesizer",
        "cursor/composer-2.5",
        api_role_with_cursor_on_api_profile=True,
    )
    assert isinstance(verdict, CheckReviewAdmissionReject)
    assert verdict.code == "substrate_unsupported_for_role"


def test_workflow_registry_conformance_passes_on_bound_policy() -> None:
    assert verify_workflow_registry_conformance() == []
