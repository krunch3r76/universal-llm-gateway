"""Commit-to-activation history and current attribution projection."""

from __future__ import annotations

from .close import apply_close_validation, current_validation
from .lifecycle import (
    mint_pending_validation_for_intent,
    repair_supersession_pairs,
    sweep_stale_pending_validations,
)
from .model import PropagationValidation
from .queries import (
    bind_validation_to_row,
    get_validation,
    latest_validation,
    latest_validation_for_intent,
    pending_unbound_validation_for_ref,
    pending_validation_for_row,
    pending_validations,
)
from .records import advance_validation, record_validation, set_kill_boundary

__all__ = [
    "PropagationValidation",
    "advance_validation",
    "apply_close_validation",
    "bind_validation_to_row",
    "current_validation",
    "get_validation",
    "latest_validation",
    "latest_validation_for_intent",
    "mint_pending_validation_for_intent",
    "pending_unbound_validation_for_ref",
    "pending_validation_for_row",
    "pending_validations",
    "record_validation",
    "repair_supersession_pairs",
    "set_kill_boundary",
    "sweep_stale_pending_validations",
]
