"""
Per-model admission control for Master Stargate.

Tracks concurrency capacity per (gateway_id, model_id) and provides
event-driven slot reservation/release for request admission.
"""

from .consumer import CapacityReleaseConsumer
from .ledger import CapacityLedger
from .queue import AdmissionQueue

__all__ = ["CapacityLedger", "AdmissionQueue", "CapacityReleaseConsumer"]
