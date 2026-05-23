"""Background-task body for the grokbuild execution tracker.

Extracted from ``tracker.py`` to keep that module under the 300 SLOC
ceiling (review §SLOC). The function lives here as a free coroutine so
the tracker module retains the public-surface code paths (start /
status / stream / cancel / cleanup_orphans / drain) without the
~100-line runner body inflating its line count.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from grokbuild.api_dispatch import api_dispatch_op
from grokbuild.dispatch import dispatch_op
from universal_logging import get_logger

from services.grokbuild_worker.events import (
    GrokbuildDispatchCompleted,
    publish_nowait,
)
from services.grokbuild_worker.tracker_state import Entry, iso_now

if TYPE_CHECKING:
    from services.grokbuild_worker.tracker import GrokbuildExecutionTracker

logger = get_logger(__name__)


async def run_dispatch_task(tracker: GrokbuildExecutionTracker, entry: Entry) -> None:
    """Invoke ``dispatch_op`` and project the result onto the tracker entry.

    Behaviour:
    * ``state`` transitions: pending → running → (succeeded | failed | cancelled).
    * Cancel-requested entries land in ``cancelled`` regardless of envelope status.
    * On any ``dispatch_op`` crash the entry becomes ``failed`` with a
      ``dispatch_crashed:`` error tag so the operator can disambiguate
      lib-internal blowups from grok-CLI failures.
    * Always fans out a terminal event and closes SSE subscribers; the
      Event Service ``grokbuild.dispatch.completed`` signal fires once.
    """
    req = entry.request
    t0 = time.monotonic()
    entry.state = "running"
    entry.updated_at = iso_now()
    try:
        if not req.mcp:
            # mcp=False: direct LLM API call — no subprocess, no MCP tooling inside dispatch.
            envelope = await api_dispatch_op(
                cwd=req.cwd,
                prompt=req.prompt,
                system_context=req.system_context,
                model=req.model,
                session_id=req.session_id,
                dispatch_id=entry.dispatch_id,
                timeout_seconds=req.timeout_seconds,
            )
        else:
            envelope = await dispatch_op(
                cwd=req.cwd,
                prompt=req.prompt,
                mode=req.mode,
                system_context=req.system_context,
                model=req.model,
                session_id=req.session_id,
                continue_recent=req.continue_recent,
                output_format=req.output_format,
                timeout_seconds=req.timeout_seconds,
                tier=req.tier,
                reasoning_effort=req.reasoning_effort,
                effort=req.effort,
                check=req.check,
                no_subagents=req.no_subagents,
                disable_web_search=req.disable_web_search,
                max_turns=req.max_turns,
                best_of_n=req.best_of_n,
                resume_strict=req.resume_strict,
                dispatch_id=entry.dispatch_id,
                proc_pid_holder=entry.pid_holder,
                recursion_depth=req.recursion_depth,
            )
    except asyncio.CancelledError:
        _finalize(
            tracker,
            entry,
            state="cancelled",
            error="task_cancelled",
            outcome="cancelled",
            duration_s=time.monotonic() - t0,
            exit_code=None,
        )
        raise
    except Exception as exc:  # noqa: BLE001 — surface any lib failure
        logger.exception("dispatch_op crashed for %s", entry.dispatch_id)
        _finalize(
            tracker,
            entry,
            state="failed",
            error=f"dispatch_crashed: {exc}",
            outcome="server_error",
            duration_s=time.monotonic() - t0,
            exit_code=None,
        )
        return

    status = envelope.get("status", "")
    if entry.cancel_requested:
        terminal_state = "cancelled"
    elif status == "completed":
        terminal_state = "succeeded"
    else:
        terminal_state = "failed"
    entry.envelope = envelope
    meta = envelope.get("metadata", {})
    outcome = (
        "cancelled"
        if terminal_state == "cancelled"
        else ("success" if terminal_state == "succeeded" else "external_failure")
    )
    _finalize(
        tracker,
        entry,
        state=terminal_state,
        error=meta.get("reason") or None,
        outcome=outcome,
        duration_s=time.monotonic() - t0,
        exit_code=envelope.get("exit_code"),
        progress_summary=status or entry.progress_summary,
    )


def _finalize(
    tracker: GrokbuildExecutionTracker,
    entry: Entry,
    *,
    state: str,
    error: str | None,
    outcome: str,
    duration_s: float,
    exit_code: int | None,
    progress_summary: str | None = None,
) -> None:
    """Single-spot finalizer: terminal fields + completed event + fanout close."""
    entry.state = state  # type: ignore[assignment]
    entry.error = error
    entry.exit_code = exit_code
    if progress_summary is not None:
        entry.progress_summary = progress_summary
    entry.completed_at = iso_now()
    entry.completed_monotonic = time.monotonic()
    entry.updated_at = entry.completed_at
    publish_nowait(
        GrokbuildDispatchCompleted(
            dispatch_id=entry.dispatch_id,
            outcome=outcome,
            duration_s=duration_s,
            exit_code=exit_code,
        )
    )
    event: dict[str, Any] = {
        "type": "completed",
        "outcome": outcome,
        "exit_code": exit_code,
    }
    # Public collaborator methods (S3): fanout pushes the terminal event to
    # subscribers and updates last_event; close_subscribers then drains
    # listener queues with the sentinel.
    tracker.fanout(entry, event)
    tracker.close_subscribers(entry)
