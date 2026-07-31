"""Tests for the shared semantic executor-knob policy."""

from __future__ import annotations

from dispatch_knob_policy import recommend_knobs, validate_knobs


def test_recommend_mechanical_light_bounded() -> None:
    rec = recommend_knobs(contract="light-bounded")
    assert rec.status == "recommended"
    assert rec.model == "composer-2.5"
    assert rec.thinking == "false"
    assert rec.effort == "low"
    assert rec.rationale_code == "mechanical_cost_control"


def test_recommend_mechanical_pure_mechanical() -> None:
    rec = recommend_knobs(contract="pure-mechanical")
    assert rec.status == "recommended"
    assert rec.knob_dict() == {"effort": "low", "thinking": "false"}


def test_recommend_none_for_implement() -> None:
    rec = recommend_knobs(contract="implement")
    assert rec.status == "none"
    assert rec.model is None
    assert rec.thinking is None
    assert rec.effort is None
    assert rec.knob_dict() == {}
    assert rec.rationale_code == "no_policy_opinion"


def test_validate_valid_on_opus() -> None:
    result = validate_knobs(
        model_id="claude-opus-4-8", knobs={"effort": "low", "thinking": "false"}
    )
    assert result == {"effort": "valid", "thinking": "valid"}


def test_validate_unsupported_on_composer() -> None:
    # composer-2.5's descriptor only supports the `fast` knob.
    result = validate_knobs(
        model_id="composer-2.5", knobs={"effort": "low", "thinking": "false"}
    )
    assert result == {"effort": "unsupported_knob", "thinking": "unsupported_knob"}


def test_validate_invalid_value() -> None:
    result = validate_knobs(model_id="claude-opus-4-8", knobs={"effort": "ultra"})
    assert result == {"effort": "invalid_value"}
