"""SdkFold handler table — kept out of ``sdk.py`` for the SLOC budget."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import signals
from .sdk_caller import stash_or_stamp
from .sdk_lane import stash_or_stamp_branch, stash_or_stamp_lane
from .sdk_provenance import (
    on_duplicate_refused,
    on_lease_acquired,
    on_lease_promoted,
    on_lease_released,
    on_park_enter,
    on_park_restore,
    on_partial_work_specimen,
    on_worker_resumed,
)
from .sdk_review_child import on_review_child_spawned, on_system_started

if TYPE_CHECKING:
    from .sdk import SdkFold


def sdk_handler_table(fold: SdkFold) -> dict[str, Any]:
    """Return this fold's signal-to-handler table."""
    table: dict[str, Any] = {}
    for signal in (
        signals.MONITOR_META_SDK_STARTED,
        signals.SDK_PIPELINE_STARTED,
        signals.SDK_WORKER_DISPATCHED,
    ):
        table[signal] = fold._on_started
    table[signals.SDK_WORKER_PROGRESS] = fold._on_progress
    table[signals.SDK_WORKER_TOOLCALL] = fold._on_toolcall
    for signal in sorted(signals.SDK_TERMINAL_SIGNALS):
        table[signal] = fold._on_terminal
    table[signals.SDK_WORKER_QUEUED] = fold._on_queued
    table[signals.SDK_GENERATE_REQUESTED] = fold._on_generate_requested
    table[signals.SDK_WORKER_TIMEOUT] = fold._on_timeout
    table[signals.SDK_WORKER_ORPHANED] = fold._on_orphaned
    table[signals.SDK_WORKER_CANCELLED] = fold._on_cancelled
    table[signals.SDK_WORKER_DELIVERY_FAILED] = fold._on_delivery_failed
    table[signals.SDK_LEASE_ACQUIRED] = lambda record: on_lease_acquired(fold, record)
    table[signals.SDK_LEASE_PROMOTED] = lambda record: on_lease_promoted(fold, record)
    table[signals.SDK_LEASE_RELEASED] = lambda record: on_lease_released(fold, record)
    table[signals.SDK_LEASE_PARK_ENTER] = lambda record: on_park_enter(fold, record)
    table[signals.SDK_LEASE_PARK_RESTORE] = lambda record: on_park_restore(fold, record)
    table[signals.SDK_CLOSEOUT_RELOCATED] = fold._on_closeout_relocated
    table[signals.SDK_CLOSEOUT_RECONCILED] = fold._on_closeout_reconciled
    table[signals.SDK_CLOSEOUT_RELAYED] = fold._on_closeout_relayed
    table[signals.SDK_CLOSEOUT_PARTIAL_WORK_PRODUCTION_SPECIMEN] = (
        lambda record: on_partial_work_specimen(fold, record)
    )
    table[signals.SDK_WORKER_RESUMED] = lambda record: on_worker_resumed(fold, record)
    table[signals.SDK_ADMIT_DUPLICATE_REFUSED] = (
        lambda record: on_duplicate_refused(fold, record)
    )
    table[signals.SDK_REVIEW_CHILD_SPAWNED] = (
        lambda record: on_review_child_spawned(fold, record)
    )
    table[signals.SDK_IMPLEMENT_SOURCE_REF_UNRESOLVED] = (
        fold._on_implement_source_ref_unresolved
    )
    table[signals.SDK_LANE_SELECTED] = lambda record: stash_or_stamp_lane(fold, record)
    table[signals.SDK_LANE_B_MINTED] = lambda record: stash_or_stamp_branch(
        fold, record
    )
    table[signals.SYSTEM_STARTED] = lambda record: on_system_started(fold, record)
    table[signals.MCP_TEAM_DISPATCH_DISPATCHED] = lambda record: stash_or_stamp(
        fold, record
    )
    return table
