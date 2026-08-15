"""Restart intent lifecycle status constants and status-class partitions.

``_BLOCKS_NEW_RESTART`` statuses hold the restart-mutex coalescing predicate and
the partial unique index on ``restart_intents``. ``_NEEDS_RECONCILE`` is the
boot ``pending_intents()`` feed — it includes ``verifying_activation`` so manage
can resume activation proof without a second begin-drain.
"""

from __future__ import annotations

STATUS_PENDING_DRAIN = "pending_drain"
STATUS_DRAINED_RESTARTING = "drained_restarting"
STATUS_VERIFYING_ACTIVATION = "verifying_activation"
STATUS_COMPLETED = "completed"
STATUS_ACTIVATION_UNVERIFIED = "activation_unverified"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_FORCE_REQUESTED = "force_requested"
STATUS_CANCELLED = "cancelled"

_BLOCKS_NEW_RESTART = (STATUS_PENDING_DRAIN, STATUS_DRAINED_RESTARTING)
_NEEDS_RECONCILE = _BLOCKS_NEW_RESTART + (STATUS_VERIFYING_ACTIVATION,)
_TERMINAL = frozenset(
    {
        STATUS_COMPLETED,
        STATUS_ACTIVATION_UNVERIFIED,
        STATUS_FAILED,
        STATUS_TIMEOUT,
        STATUS_FORCE_REQUESTED,
        STATUS_CANCELLED,
    }
)
_ALL_STATUSES = frozenset(_NEEDS_RECONCILE) | _TERMINAL

__all__ = [
    "STATUS_ACTIVATION_UNVERIFIED",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_DRAINED_RESTARTING",
    "STATUS_FAILED",
    "STATUS_FORCE_REQUESTED",
    "STATUS_PENDING_DRAIN",
    "STATUS_TIMEOUT",
    "STATUS_VERIFYING_ACTIVATION",
    "_ALL_STATUSES",
    "_BLOCKS_NEW_RESTART",
    "_NEEDS_RECONCILE",
    "_TERMINAL",
]
