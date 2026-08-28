"""Round-trip + alias contract for the canonical effort vocabulary."""

from __future__ import annotations

import pytest

from effort_vocabulary import (
    EFFORT_TOKENS,
    WIRE_LADDER,
    normalize_effort,
    to_picker_label,
    to_picker_suffix,
    to_testid,
    to_wire,
)


@pytest.mark.offline
@pytest.mark.parametrize("rung", list(WIRE_LADDER))
def test_wire_picker_testid_round_trip(rung: str) -> None:
    """wire → picker suffix → testid → wire is identity for every rung."""
    picker = to_picker_suffix(rung)
    assert picker is not None
    testid = to_testid(picker)
    assert testid is not None
    # testid encodes the wire form for Extra (effort-option-xhigh).
    recovered = "xhigh" if testid.endswith("xhigh") else picker
    if picker == "extra":
        recovered = "xhigh"
    assert to_wire(recovered) == rung
    assert to_wire(rung) == rung


@pytest.mark.offline
@pytest.mark.parametrize(
    ("alias", "wire"),
    [
        ("extra", "xhigh"),
        ("extra-high", "xhigh"),
        ("extra high", "xhigh"),
        ("Extra High", "xhigh"),
        ("extrahigh", "xhigh"),
    ],
)
def test_extra_aliases_normalize_to_xhigh(alias: str, wire: str) -> None:
    assert normalize_effort(alias) == wire
    assert to_picker_suffix(alias) == "extra"
    assert to_testid(alias) == "effort-option-xhigh"


@pytest.mark.offline
def test_effort_tokens_cover_wire_and_picker() -> None:
    assert {"low", "medium", "high", "xhigh", "extra", "max"} <= EFFORT_TOKENS
    assert {"none", "minimal"} <= EFFORT_TOKENS


@pytest.mark.offline
def test_unknown_effort_returns_none() -> None:
    assert normalize_effort("turbo") is None
    assert to_picker_suffix("") is None
    assert to_testid(None) is None
    assert to_picker_label(None) is None
    assert to_picker_label("turbo") is None


@pytest.mark.offline
@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("max", "Max"),
        ("high", "High"),
        ("extra", "Extra"),
        ("xhigh", "Extra"),
        ("medium", "Medium"),
        ("low", "Low"),
    ],
)
def test_picker_label_for_cowork_flyout(raw: str, label: str) -> None:
    assert to_picker_label(raw) == label
