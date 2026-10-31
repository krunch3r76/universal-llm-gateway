"""Offline tests — code-lane check/review default is anthropic sonnet (API)."""

from __future__ import annotations

import pytest

from implement_admission.check_review_substrate import (
    CHECK_REVIEW_DECISION_CITATION,
    CURSOR_CHECK_REVIEW_MODELS,
    coerce_check_review_omit_to_cursor_seat,
    is_cursor_check_review_model,
    load_check_review_default_model,
    resolve_check_review_model,
)
from implement_admission.routing import load_route_policy
from implement_admission.workflow_registry import (
    CHECK_REVIEW_WORKFLOW,
    verify_workflow_registry_conformance,
)

pytestmark = pytest.mark.offline


def test_standing_default_is_anthropic_sonnet() -> None:
    policy = load_route_policy()
    assert load_check_review_default_model(policy) == "anthropic/claude-sonnet-5-5"
    assert (
        CHECK_REVIEW_DECISION_CITATION in "decision:code-review-panel-cursor-substrate"
    )
    entry = policy["workflows"][CHECK_REVIEW_WORKFLOW]
    assert entry["model"] == "anthropic/claude-sonnet-5-5"
    assert entry["seat"] == "reviewer"
    assert "cursor/muse-spark-1.3" not in entry["model"]
    assert "cursor/gpt-5.6-terra" not in entry["model"]


def test_cursor_check_review_allowlist_is_five_ids_without_composer_or_argon() -> None:
    assert CURSOR_CHECK_REVIEW_MODELS == frozenset(
        {
            "cursor/muse-spark-1.3",
            "cursor/claude-fable-5-1",
            "cursor/gpt-5.6-terra",
            "cursor/gpt-5.6-sol",
            "cursor/gpt-5.6-luna",
        }
    )
    assert "cursor/composer-2.5" not in CURSOR_CHECK_REVIEW_MODELS
    assert "cursor/gemini-4-argon" not in CURSOR_CHECK_REVIEW_MODELS
    for model in CURSOR_CHECK_REVIEW_MODELS:
        assert is_cursor_check_review_model(model) is True


def test_route_policy_conformance() -> None:
    assert verify_workflow_registry_conformance() == []


def test_resolve_reviewer_omit_uses_anthropic_default_on_api() -> None:
    resolution = resolve_check_review_model("reviewer", None)
    assert resolution.resolved_model == "anthropic/claude-sonnet-5-5"
    assert resolution.substrate == "api"
    assert resolution.delivery_from_role == "reviewer"


def test_coerce_does_not_force_cursor_sdk_when_default_is_anthropic() -> None:
    role, seat, model, coerced = coerce_check_review_omit_to_cursor_seat(
        "reviewer", None, None
    )
    assert coerced is False
    assert role == "reviewer"
    assert seat is None
    assert model is None


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
