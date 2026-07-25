"""Nest park/restore coordination for cursor-sdk write-lease + capacity.

Ledger park columns and FifoCapacityGate.transfer_holder must both observe
enter/restore (PARK-RESTORE-DUAL). Ordinary release/force_release wakes FIFO
waiters and must not run while a parked parent waits for the child.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.cursor_sdk_events import (
    emit_write_lease_park_enter,
    emit_write_lease_park_restore,
)
from services.git_integration_worker.cursor_sdk_gate import (
    force_release_sdk_dispatch_slot,
    release_sdk_dispatch_slot,
    transfer_sdk_dispatch_slot,
    transfer_sdk_dispatch_slot_sync,
)

logger = get_logger(__name__)

ReleaseDisposition = Literal["restored", "released"]


async def transfer_capacity_after_park(
    *, parent_id: str, child_id: str, source_repo: str | None
) -> None:
    """Move capacity from parked parent to nested child without waking waiters.

    Emits ``frontier.sdk.worker.lease.park_enter`` after a successful transfer.
    """
    await transfer_sdk_dispatch_slot(from_id=parent_id, to_id=child_id)
    emit_write_lease_park_enter(
        parent_id=parent_id,
        child_id=child_id,
        source_repo=source_repo,
    )


async def release_or_restore_for_child(*, dispatch_id: str) -> ReleaseDisposition:
    """Child capacity exit: restore parked parent via transfer, else release.

    A1: never wake FIFO waiters while a ``parked_waiting`` parent points at
    this child — transfer_holder(child→parent) instead.
    """
    ledger = CursorDispatchLedger.instance()
    parked = await asyncio.to_thread(
        ledger.find_parked_parent_for_child, child_id=dispatch_id
    )
    if parked is not None:
        parent_id, source_repo = parked
        try:
            await transfer_sdk_dispatch_slot(from_id=dispatch_id, to_id=parent_id)
        except Exception:
            # Child may already have released via worker finally; still restore ledger.
            logger.warning(
                "park restore transfer failed (may already be transferred): "
                "child=%s parent=%s",
                dispatch_id[:8],
                parent_id[:8],
                exc_info=True,
            )
        restored_repo = await asyncio.to_thread(
            ledger.restore_from_park, parent_id=parent_id
        )
        emit_write_lease_park_restore(
            parent_id=parent_id,
            child_id=dispatch_id,
            source_repo=restored_repo or source_repo,
        )
        return "restored"
    await force_release_sdk_dispatch_slot(dispatch_id=dispatch_id)
    return "released"


def release_or_restore_for_child_sync(
    loop: asyncio.AbstractEventLoop, *, dispatch_id: str
) -> ReleaseDisposition:
    """Worker-thread finally path for park-aware capacity exit (A1).

    Restores a parked parent via ``transfer_holder`` when present; otherwise
    performs an ordinary release on the owning event loop.
    """
    ledger = CursorDispatchLedger.instance()
    parked = ledger.find_parked_parent_for_child(child_id=dispatch_id)
    if parked is not None:
        parent_id, source_repo = parked
        try:
            transfer_sdk_dispatch_slot_sync(
                loop, from_id=dispatch_id, to_id=parent_id
            )
        except Exception:
            logger.warning(
                "park restore sync transfer failed: child=%s parent=%s",
                dispatch_id[:8],
                parent_id[:8],
                exc_info=True,
            )
        restored_repo = ledger.restore_from_park(parent_id=parent_id)
        emit_write_lease_park_restore(
            parent_id=parent_id,
            child_id=dispatch_id,
            source_repo=restored_repo or source_repo,
        )
        return "restored"
    fut = asyncio.run_coroutine_threadsafe(
        release_sdk_dispatch_slot(dispatch_id=dispatch_id), loop
    )
    fut.result(timeout=30.0)
    return "released"
