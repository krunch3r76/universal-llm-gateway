"""Tests for check/review dual-substrate admission (dispatch-dual-substrate-option)."""

from __future__ import annotations

import pytest

from implement_admission.check_review_substrate import (
    CHECK_REVIEW_DEFAULT_MODEL,
    CheckReviewAdmissionReject,
    evaluate_check_review_admission,
    resolve_check_review_model,
    verify_check_review_default_conformance,
)
from implement_admission.routing import load_route_policy, verify_check_review_default_policy


def test_omit_model_resolves_reviewer_to_terra() -> None:
    resolution = resolve_check_review_model("reviewer", None)
    assert resolution.resolved_model == CHECK_REVIEW_DEFAULT_MODEL
    assert resolution.substrate == "api"


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


def test_default_key_conformance_passes_on_bound_policy() -> None:
    policy = load_route_policy()
    assert verify_check_review_default_conformance(policy) == []


def test_default_key_flip_without_decision_citation_fails() -> None:
    policy = dict(load_route_policy())
    policy["check_review_default_model"] = "openai/gpt-5.6-sol"
    errors = verify_check_review_default_conformance(policy, policy_text="no citations")
    assert errors


def test_verify_check_review_default_policy_live_file() -> None:
    assert verify_check_review_default_policy() == []
