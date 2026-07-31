"""Unit tests for pure CDP model-request matching (friction 24969)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_model_match import (
    family_nested_in_more_models,
    label_satisfies_request,
    normalize_picker_request,
    parse_model_request,
    sealed_ask_default_effort,
)


@pytest.mark.offline
@pytest.mark.parametrize(
    ("requested", "label", "expected"),
    [
        ("fable-5-max", "Fable 5 High", False),
        ("fable-5-max", "Fable 5 Max", True),
        ("opus-5-max", "Opus 5 High", False),
        ("opus-5-max", "Opus 5 Max", True),
        ("opus-5-high", "Opus 5 High", True),
        ("opus-5-high", "Opus 5 Max", False),
        ("opus-5-high", "Opus 5 Extra High", False),
        ("opus-5-extra", "Opus 5 Extra High", True),
        ("opus-5-extra", "Opus 5 High", False),
        ("opus-5-extra", "Opus 5 Max", False),
        ("fable-5-max", "Opus 5 Max", False),
    ],
)
def test_label_satisfies_request_effort_rungs(
    requested: str, label: str, expected: bool
) -> None:
    assert label_satisfies_request(requested, label) is expected


@pytest.mark.offline
def test_normalize_picker_request_strips_cdp_prefix() -> None:
    assert normalize_picker_request("cdp/opus-5") == "opus-5"
    assert normalize_picker_request("cdp/fable-5") == "fable-5"
    assert normalize_picker_request("cdp/fable") == "fable-5"
    assert normalize_picker_request("fable") == "fable-5"
    assert normalize_picker_request("opus-5") == "opus-5"


@pytest.mark.offline
def test_parse_model_request_max_effort() -> None:
    family, effort = parse_model_request("fable-5-max")
    assert family == "fable-5"
    assert effort == "max"


@pytest.mark.offline
@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("fable-5", "high"),
        ("fable-5-max", "high"),
        ("opus-5", "high"),
        ("sonnet-5", None),
        ("haiku-4.5", None),
    ],
)
def test_sealed_ask_default_effort(family: str, expected: str | None) -> None:
    assert sealed_ask_default_effort(family) == expected


@pytest.mark.offline
@pytest.mark.parametrize(
    ("requested", "label", "expected"),
    [
        ("fable-5", "Fable 5 Max", False),
        ("fable-5", "Fable 5 High", True),
    ],
)
def test_label_satisfies_bare_fable_with_bound_high(
    requested: str, label: str, expected: bool
) -> None:
    assert label_satisfies_request(requested, label, effort="high") is expected


@pytest.mark.offline
def test_family_nested_in_more_models_fable_only() -> None:
    assert family_nested_in_more_models("fable") is True
    assert family_nested_in_more_models("fable-5") is True
    assert family_nested_in_more_models("opus-5") is False
