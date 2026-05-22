"""Cancel-path helper for ``GrokbuildExecutionTracker.cancel``.

The cancel flow is ~50 SLOC of process-group signal handling and task
shielding that would otherwise inflate ``tracker.py`` past its 300-SLOC
ceiling. Extracted as a free function rather than a method so the tracker
class stays focused on lifecycle state and SSE fanout.

Signal contract: SIGTERM → wait 30s → SIGKILL (operator answer 1c). Client
disconnect on an SSE/poll connection does NOT enter this path — only an
explicit ``DELETE /dispatches/{id}`` does.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Any

from services.grokbuild_worker.tracker_state import Entry

SIGTERM_GRACE_SECONDS = 30.0


async def cancel_entry(entry: Entry) -> tuple[str, int, dict[str, Any]]:
    """Run the SIGTERM → grace → SIGKILL cancel sequence on ``entry``.

    Returns ``(signal_used, status_code, body)`` — the caller (the
    tracker class) attaches the ``publish_nowait`` cancel event and the
    JSONResponse construction. ``signal_used`` is exposed so the event
    payload can distinguish "SIGTERM landed" from "SIGKILL was needed"
    from "task was still pre-spawn".
    """
    if entry.is_terminal:
        return (
            "noop",
            409,
            {
                "reason_code": "already_terminal",
                "reason": f"dispatch in state {entry.state}",
            },
        )
    entry.cancel_requested = True
    pid = entry.pid
    signal_used = "SIGTERM"
    if pid is not None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        try:
            if entry.task is not None:
                await asyncio.wait_for(
                    asyncio.shield(entry.task), timeout=SIGTERM_GRACE_SECONDS
                )
        except TimeoutError:
            signal_used = "SIGKILL"
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            with contextlib.suppress(asyncio.CancelledError, RuntimeError, OSError):
                if entry.task is not None:
                    await asyncio.wait_for(asyncio.shield(entry.task), timeout=5.0)
    else:
        # Subprocess hadn't been spawned yet — cancel the task directly.
        if entry.task is not None and not entry.task.done():
            entry.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await entry.task
        signal_used = "task_cancel"

    return (
        signal_used,
        200,
        {
            "dispatch_id": entry.dispatch_id,
            "state": entry.state,
            "signal_used": signal_used,
            "reason": "operator_cancel",
        },
    )
