"""Offline tests — code-lane check/review default is cursor/ (B)."""

from __future__ import annotations

import pytest

from implement_admission.check_review_substrate import (
    CHECK_REVIEW_DEFAULT_MODEL,
    CHECK_REVIEW_DECISION_CITATION,
    coerce_check_review_omit_to_cursor_seat,
    families_independently_measured,
    independence_family,
    load_check_review_default_model,
    resolve_check_review_model,
    verify_check_review_default_conformance,
)
from implement_admission.routing import (
    load_route_policy,
    verify_check_review_default_policy,
)

pytestmark = pytest.mark.offline


def test_standing_default_is_cursor_terra() -> None:
    assert CHECK_REVIEW_DEFAULT_MODEL == "cursor/gpt-5.6-terra"
    policy = load_route_policy()
    assert load_check_review_default_model(policy) == "cursor/gpt-5.6-terra"
    assert CHECK_REVIEW_DECISION_CITATION in "decision:code-review-panel-cursor-substrate"


def test_route_policy_conformance() -> None:
    assert verify_check_review_default_policy() == []


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


def test_independence_family_ignores_cursor_substrate() -> None:
    assert independence_family("cursor/gpt-5.6-terra") == "openai"
    assert independence_family("cursor/claude-opus-5") == "anthropic"
    assert independence_family("cursor/composer-2.5") == "composer"
    assert independence_family("openai/gpt-5.6-terra") == "openai"


def test_independence_family_cdp_fable_is_anthropic() -> None:
    """CDP picker slugs measure Anthropic weight-class, not substrate ``cdp``."""
    assert independence_family("cdp/fable") == "anthropic"
    assert independence_family("cdp/fable-5") == "anthropic"
    assert independence_family("cdp/opus-5") == "anthropic"
    assert independence_family("cursor/claude-fable-5") == "anthropic"
    assert independence_family("cdp/fable") == independence_family(
        "cursor/claude-opus-5"
    )
    assert not families_independently_measured(
        independence_family("cdp/fable"),
        independence_family("cursor/claude-opus-5"),
    )


def test_independence_family_unmeasured_is_unknown() -> None:
    assert independence_family("cdp/not-a-real-model") == "unknown"
    assert independence_family("cursor/mystery-9") == "unknown"
    assert not families_independently_measured("unknown", "openai")
    assert not families_independently_measured("anthropic", "unknown")
    assert families_independently_measured("anthropic", "openai") is True
    assert families_independently_measured("anthropic", "anthropic") is False


def test_conformance_requires_decision_when_drifted() -> None:
    errors = verify_check_review_default_conformance(
        {"check_review_default_model": "openai/gpt-5.6-terra"},
        policy_text="# no decision citation",
    )
    assert errors
    assert "decision:" in errors[0] or "differs" in errors[0]
