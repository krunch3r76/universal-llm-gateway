"""Public exports for prose_fact_scan."""

from __future__ import annotations

from .gate import filter_active_eligible, is_gate_eligible, is_temporally_active
from .scanner import run_prose_fact_scan
from .target_resolver import expand_tier_a, is_hard_excluded, resolve_scan_targets

__all__ = [
    "expand_tier_a",
    "filter_active_eligible",
    "is_gate_eligible",
    "is_hard_excluded",
    "is_temporally_active",
    "resolve_scan_targets",
    "run_prose_fact_scan",
]
