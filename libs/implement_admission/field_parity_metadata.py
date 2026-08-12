"""Parity classification metadata on ``PropagationRow`` field declarations."""

from __future__ import annotations

from typing import Any

from implement_admission.propagation_row import PropagationRow

# Fail-closed: unclassified row fields are effect-class (REFUSE on drop).
_DEFAULT_PARITY_CLASS = "effect"


def parity_class_for_row_field(field_name: str) -> str:
    """Return the parity class bound on *field_name* — ``effect`` when absent."""
    info = PropagationRow.model_fields.get(field_name)
    if info is None:
        return "unknown"
    extra: Any = info.json_schema_extra or {}
    if not isinstance(extra, dict):
        return _DEFAULT_PARITY_CLASS
    return str(extra.get("parity", _DEFAULT_PARITY_CLASS))


def propagation_row_field_names() -> frozenset[str]:
    """Declared ``PropagationRow`` field names (the bound-side vocabulary)."""
    return frozenset(PropagationRow.model_fields.keys())


__all__ = [
    "parity_class_for_row_field",
    "propagation_row_field_names",
]
