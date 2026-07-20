"""Timing and threshold constants for periodic VRAM reconciliation sweeps."""

from typing import Final

RECONCILE_INTERVAL_S: Final[float] = 60.0
VRAM_DISCREPANCY_THRESHOLD_MB: Final[int] = 2000
