"""Runtime configuration for cortex_store — env-overridable settings."""

from __future__ import annotations

import os

_VALID_SUPERSEDE_VALIDATION_MODES = frozenset({"shadow", "hard_422"})
_DEFAULT_SUPERSEDE_VALIDATION_MODE = "shadow"


def supersede_validation_mode() -> str:
    """Return supersede quality-validation mode: ``shadow`` (default) or ``hard_422``."""
    raw = os.environ.get(
        "SUPERSEDE_VALIDATION_MODE", _DEFAULT_SUPERSEDE_VALIDATION_MODE
    ).strip()
    if raw not in _VALID_SUPERSEDE_VALIDATION_MODES:
        return _DEFAULT_SUPERSEDE_VALIDATION_MODE
    return raw


__all__ = ["supersede_validation_mode"]
