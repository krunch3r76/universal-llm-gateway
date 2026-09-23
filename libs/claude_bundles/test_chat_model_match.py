"""Unit tests for pure CDP model-request matching (friction 24969)."""

from __future__ import annotations

import pytest

from claude_bundles.chat_model_match import (
    compose_cdp_model_with_effort,
    effort_only_chip_change,
    family_attested,
    family_nested_in_more_models,
    label_satisfies_request,
    match_effort_qualified_radio,
    menu_label_glued,
    normalize_picker_request,
    parse_model_request,
    prefer_model_name_index,
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
    assert normalize_picker_request("cdp/fable-5.1") == "fable-5.1"
    assert normalize_picker_request("cdp/fable") == "fable-5.1"
    assert normalize_picker_request("fable") == "fable-5.1"
    assert normalize_picker_request("opus-5") == "opus-5"


@pytest.mark.offline
@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("cdp/opus-5", "max", "cdp/opus-5-max"),
        ("cdp/opus-5", "extra", "cdp/opus-5-extra"),
        ("cdp/opus-5", "xhigh", "cdp/opus-5-extra"),
        ("cdp/opus-5", "high", "cdp/opus-5-high"),
        ("cdp/opus-5", None, "cdp/opus-5"),
        ("cdp/opus-5", "", "cdp/opus-5"),
        ("cdp/opus-5-max", "high", "cdp/opus-5-max"),
        ("cdp/fable", "max", "cdp/fable-5.1-max"),
        ("cdp/fable-5", "max", "cdp/fable-5-max"),
        ("cdp/fable-5.1", "max", "cdp/fable-5.1-max"),
        ("cdp/sonnet-5", "max", "cdp/sonnet-5-max"),
        ("cdp/sonnet-5", "extra", "cdp/sonnet-5-extra"),
    ],
)
def test_compose_cdp_model_with_effort(
    model: str, effort: str | None, expected: str
) -> None:
    assert compose_cdp_model_with_effort(model, effort) == expected


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
        ("fable-5.1", "high"),
        ("opus-5", "high"),
        ("sonnet-5", "extra"),
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


@pytest.mark.offline
def test_match_effort_qualified_radio_clicks_high_sku() -> None:
    """a:30693 — High is a first-class radio; do not pick Extra/Max for high."""
    labels = ["Opus 5", "Opus 5 High", "Opus 5 Extra High", "Opus 5 Max"]
    assert (
        match_effort_qualified_radio("opus-5", labels, effort="high") == "Opus 5 High"
    )
    assert (
        match_effort_qualified_radio("opus-5", labels, effort="extra")
        == "Opus 5 Extra High"
    )
    assert match_effort_qualified_radio("opus-5", labels, effort="max") == "Opus 5 Max"
    assert match_effort_qualified_radio("opus-5", labels, effort=None) is None
    assert match_effort_qualified_radio("opus-5", ["Opus 5"], effort="high") is None


@pytest.mark.offline
def test_prefer_fable_name_over_parent_and_glued_subtitle() -> None:
    rows = [
        {
            "own": (
                "Opus 5.5Most capable for ambitious work"
                "Fable 5.1For your toughest challenges"
            ),
            "full": "parent",
        },
        {
            "own": "Fable 5.1For your toughest challenges",
            "full": "Fable 5.1For your toughest challenges",
        },
        {"own": "Fable 5.1", "full": "Fable 5.1"},
    ]
    assert prefer_model_name_index("fable-5.1", rows) == 2
    assert menu_label_glued(rows[1]["own"]) is True
    assert menu_label_glued("Fable 5.1") is False


@pytest.mark.offline
def test_prefer_opus_keeps_first_dom_name() -> None:
    """Same click bug must not reorder Opus 5.5 ahead of an older Opus row."""
    rows = [
        {"own": "Opus 5.5", "full": "Opus 5.5Most capable for ambitious work"},
        {"own": "Opus 5", "full": "Opus 5"},
    ]
    assert prefer_model_name_index("opus-5", rows) == 0


@pytest.mark.offline
def test_family_attest_ignores_effort_only_opus_change() -> None:
    assert family_attested("fable-5.1", "Model: Opus 5.5 High") is False
    assert family_attested("fable-5.1", "Model: Fable 5.1") is True
    assert (
        effort_only_chip_change("Model: Opus 5.5 Medium", "Model: Opus 5.5 High")
        is True
    )
    assert (
        label_satisfies_request("fable-5.1", "Model: Opus 5.5 High", effort="high")
        is False
    )
