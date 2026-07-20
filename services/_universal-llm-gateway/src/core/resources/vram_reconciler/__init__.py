"""Periodic VRAM reconciliation for phantom-model and ghost-model detection.

Phantom model: worker process running but not tracked (orphan).
Ghost model: tracked as loaded but engine subprocess dead (stale state).

Re-exports VramReconciler so existing imports from core.resources.vram_reconciler
keep working after the package-shadow split.
"""

from .constants import RECONCILE_INTERVAL_S, VRAM_DISCREPANCY_THRESHOLD_MB
from .reconciler import VramReconciler

__all__ = [
    "RECONCILE_INTERVAL_S",
    "VRAM_DISCREPANCY_THRESHOLD_MB",
    "VramReconciler",
]
