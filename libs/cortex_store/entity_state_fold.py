"""Live entity-state fold path (W4): evidence-gated claim → structured field."""

from __future__ import annotations

from cortex_store.entity_state_fold_tx import fold_claim, reverse_fold
from cortex_store.entity_state_provenance import FoldResult, state_sha256
from cortex_store.entity_state_read import FieldState, field_state

__all__ = [
    "FieldState",
    "FoldResult",
    "field_state",
    "fold_claim",
    "reverse_fold",
    "state_sha256",
]
