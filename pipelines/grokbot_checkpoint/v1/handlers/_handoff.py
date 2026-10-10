"""Successor handoff letter: re-export of the shared continuity_tape helpers."""

from continuity_tape.handoff import (
    HANDOFF_MAX_BYTES,
    handoff_binding,
    handoff_canonical_bytes,
    handoff_json_size,
    handoff_sha12,
    validate_handoff,
)

__all__ = [
    "HANDOFF_MAX_BYTES",
    "handoff_binding",
    "handoff_canonical_bytes",
    "handoff_json_size",
    "handoff_sha12",
    "validate_handoff",
]
