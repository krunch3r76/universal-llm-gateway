"""
Package-shadow exports for federated routing rejection handling.

This package preserves the former rejection.py import surface while splitting
topology diagnostics, capacity detail extraction, terminal event emission, and
no-selection orchestration into focused modules.
"""

from .capacity_details import _build_capacity_details
from .no_selection_outcome import handle_selection_rejection

__all__ = [
    "handle_selection_rejection",
    "_build_capacity_details",
]
