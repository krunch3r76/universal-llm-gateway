"""Coverage enum on ContinuityMessagesEnvelope meta."""

from __future__ import annotations

import typing

import pytest
from continuity_tape.messages import EnvelopeMeta
from pydantic import ValidationError

pytestmark = pytest.mark.offline


def test_envelope_meta_coverage_accepts_full_and_tail() -> None:
    full = EnvelopeMeta(coverage="full")
    tail = EnvelopeMeta(coverage="tail")
    assert full.coverage == "full"
    assert tail.coverage == "tail"


def test_envelope_meta_coverage_rejects_tail_only() -> None:
    with pytest.raises(ValidationError):
        EnvelopeMeta(coverage="tail_only")  # type: ignore[arg-type]


def test_envelope_meta_coverage_literal_is_full_or_tail() -> None:
    hints = typing.get_type_hints(EnvelopeMeta)
    coverage_hint = hints["coverage"]
    assert "full" in str(coverage_hint)
    assert "tail" in str(coverage_hint)
    assert "tail_only" not in str(coverage_hint)
