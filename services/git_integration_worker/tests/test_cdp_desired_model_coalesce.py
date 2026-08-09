"""Admit-time coalesce: desired_model=cdp/* → escalation=."""

from __future__ import annotations

import pytest

from services.git_integration_worker.cursor_auto.wire_map import (
    coalesce_cdp_desired_model_into_escalation,
    coerce_cdp_desired_model_alias,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("cdp/fable", "cdp/fable"),
        ("cdp/fable-5", "cdp/fable"),
        ("cdp/opus-5", "cdp/opus-5"),
        ("cdp/opus", "cdp/opus-5"),
        ("grok-4.5", None),
        ("auto", None),
    ],
)
def test_coerce_cdp_desired_model_alias(raw: str, expected: str | None) -> None:
    assert coerce_cdp_desired_model_alias(raw) == expected


def test_coalesce_moves_fable_onto_escalation() -> None:
    model, esc, meta = coalesce_cdp_desired_model_into_escalation("cdp/fable", None)
    assert model == "auto"
    assert esc == "cdp/fable"
    assert meta["coalesced"] is True


def test_coalesce_noop_when_already_escalation() -> None:
    model, esc, meta = coalesce_cdp_desired_model_into_escalation("auto", "cdp/fable")
    assert model == "auto"
    assert esc == "cdp/fable"
    assert meta["coalesced"] is False


def test_coalesce_conflict_leaves_pins() -> None:
    model, esc, meta = coalesce_cdp_desired_model_into_escalation(
        "cdp/fable", "cdp/opus-5"
    )
    assert model == "cdp/fable"
    assert esc == "cdp/opus-5"
    assert meta.get("conflict") is True
    assert meta["coalesced"] is False
