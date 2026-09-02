"""Offline tests — code-lane check/review default is cursor/ (B)."""

from __future__ import annotations

import pytest

from implement_admission.check_review_substrate import (
    CHECK_REVIEW_DECISION_CITATION,
    coerce_check_review_omit_to_cursor_seat,
    load_check_review_default_model,
    resolve_check_review_model,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    CHECK_REVIEW_WORKFLOW,
    verify_workflow_registry_conformance,
)

pytestmark = pytest.mark.offline


def test_standing_default_is_cursor_terra() -> None:
    policy = load_route_policy()
    assert load_check_review_default_model(policy) == "cursor/gpt-5.6-terra"
    assert CHECK_REVIEW_DECISION_CITATION in "decision:code-review-panel-cursor-substrate"
    entry = policy["workflows"][CHECK_REVIEW_WORKFLOW]
    assert entry["model"] == "cursor/gpt-5.6-terra"
    assert entry["seat"] == "cursor-sdk"


def test_route_policy_conformance() -> None:
    assert verify_workflow_registry_conformance() == []


def test_resolve_reviewer_omit_uses_cursor_default() -> None:
    resolution = resolve_check_review_model("reviewer", None)
    assert resolution.resolved_model == "cursor/gpt-5.6-terra"
    assert resolution.substrate == "cursor-sdk"
    assert resolution.delivery_from_role == "reviewer"


def test_coerce_omit_reviewer_to_cursor_seat() -> None:
    role, seat, model, coerced = coerce_check_review_omit_to_cursor_seat(
        "reviewer", None, None
    )
    assert coerced is True
    assert role is None
    assert seat == "cursor-sdk"
    assert model == "cursor/gpt-5.6-terra"


def test_coerce_skips_when_explicit_openai() -> None:
    role, seat, model, coerced = coerce_check_review_omit_to_cursor_seat(
        "reviewer", None, "openai/gpt-5.6-terra"
    )
    assert coerced is False
    assert role == "reviewer"
    assert model == "openai/gpt-5.6-terra"


def test_load_check_review_default_model_requires_workflow_slot() -> None:
    with pytest.raises(ValueError, match="workflows.check_review"):
        load_check_review_default_model({"workflows": {}})
