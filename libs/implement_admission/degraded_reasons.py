"""Stops-aware helpers for conductor witness fold and closeout grading."""

from __future__ import annotations

from implement_admission.conductor_witness_types import (
    STOPS_BLOCK_TOKENS,
    stops_block_reason,
)

__all__ = [
    "STOPS_BLOCK_TOKENS",
    "g4_stops_block_reason",
    "stops_block_reason",
]


def g4_stops_block_reason(tip_body: str) -> str | None:
    """Return the G4 Stops-column block token when present."""
    return stops_block_reason(tip_body, "G4")
