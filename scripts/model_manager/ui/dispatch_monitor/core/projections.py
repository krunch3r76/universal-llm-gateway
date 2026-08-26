"""Fold state → frozen rows. The projection half of ``derive``.

Split out of :mod:`.model` so that ``Model`` reads as fold-plus-derive rather than
fold-plus-derive-plus-three-row-builders. Every function here is pure and takes
``now_ms`` explicitly; none reads a clock.

Row order is a deterministic sort on the row's own key in all three cases. That is
load-bearing, not cosmetic: dict iteration order reaching the output would make the
fingerprint non-reproducible and the F2 determinism test meaningless.
"""

from __future__ import annotations

from .correlation import CorrelationIndex
from .dtos import (
    CdpLegRow,
    CharterRootRow,
    SdkDispatchRow,
    Thresholds,
)
from .folds import CdpFold, CharterFold, SdkFold

#: Root states meaning the root's own lifecycle has ended.
ROOT_CLOSED_STATES = ("closed",)

#: Dispatch states meaning the worker leg is over, one way or another.
SDK_TERMINAL_STATES = ("completed", "failed", "timeout", "orphaned", "cancelled")


def age(now_ms: int, since_ms: int | None) -> int | None:
    """Return ``now_ms - since_ms`` floored at zero, or ``None`` if unknown.

    Flooring matters: fixture timestamps and a replayed ``now_ms`` can legitimately
    put an event marginally in the future, and a negative age would render as a
    nonsense countdown.
    """
    if since_ms is None:
        return None
    return max(0, now_ms - since_ms)


def sdk_rows(
    fold: SdkFold, index: CorrelationIndex, now_ms: int
) -> tuple[SdkDispatchRow, ...]:
    """Project SDK fold state to sorted frozen rows."""
    rows = []
    for state in fold.dispatches.values():
        live = state.terminal_ms is None
        duration = (
            state.terminal_ms - state.started_ms
            if not live and state.started_ms is not None
            else None
        )
        elapsed = age(now_ms, state.started_ms) if live else None
        rows.append(
            SdkDispatchRow(
                dispatch_id=state.dispatch_id,
                state=state.state,
                root_id=state.root_id or index.root_for_dispatch(state.dispatch_id),
                thread_id=state.thread_id,
                seat=state.seat,
                role=state.role,
                model=state.model,
                contract=state.contract,
                started_ms=state.started_ms,
                last_progress_ms=state.last_progress_ms,
                terminal_ms=state.terminal_ms,
                duration_ms=duration,
                elapsed_ms=elapsed,
                idle_age_ms=age(now_ms, state.last_progress_ms) if live else None,
                prompt_tokens=state.prompt_tokens,
                completion_tokens=state.completion_tokens,
                cached_tokens=state.cached_tokens,
                stall_stage=state.stall_stage,
                failure_reason=state.failure_reason,
                emitters_seen=tuple(state.emitters_seen),
                divergent_fields=tuple(sorted(state.divergent_fields)),
                terminal_emitter=state.terminal_emitter,
                provenance=state.provenance or "signal",
                queue_position=state.queue_position,
                closeout_uri=state.closeout_uri,
                delivery_failed=state.delivery_failed,
                implement_gate_bypass=state.implement_gate_bypass,
                lease_released_without_terminal=state.lease_released_without_terminal,
                last_tool_name=state.last_tool_name,
                last_tool_status=state.last_tool_status,
                tool_call_count=state.tool_call_count,
                parent_execution_id=state.parent_execution_id,
                review_child=state.review_child,
                admitted_via=state.admitted_via,
                asked_by=state.asked_by,
                purpose=state.purpose,
                story_id=state.story_id,
                topic=state.topic,
                nest_under=state.nest_under,
                resume_of=state.resume_of,
            )
        )
    rows.sort(key=lambda r: r.dispatch_id)
    return tuple(rows)


def cdp_rows(
    fold: CdpFold, index: CorrelationIndex, now_ms: int
) -> tuple[CdpLegRow, ...]:
    """Project CDP fold state to sorted frozen rows."""
    rows = []
    for state in fold.legs.values():
        live = state.terminal_ms is None
        elapsed = age(now_ms, state.admitted_at_ms) if live else None
        rows.append(
            CdpLegRow(
                request_id=state.request_id,
                execution_id=state.execution_id,
                satellite_execution_id=state.satellite_execution_id,
                thread_id=state.thread_id,
                model=state.model,
                caller_agent=state.caller_agent,
                topic=state.topic,
                chat_url=state.chat_url,
                state=state.state,
                admitted_at_ms=state.admitted_at_ms,
                terminal_ms=state.terminal_ms,
                elapsed_ms=elapsed,
                max_wall_s=state.max_wall_s,
                archive_uri=state.archive_uri,
                content_proof_uri=state.content_proof_uri,
                stall_stage=state.stall_stage,
                failure_reason=state.failure_reason,
                proof_present=bool(state.archive_uri or state.content_proof_uri),
                root_id=state.root_id or index.root_for_cdp(state.request_id),
            )
        )
    rows.sort(key=lambda r: r.request_id)
    return tuple(rows)


def _terminal_ms_for_current_worker(
    root_id: str,
    worker_thread: str | None,
    dispatches: tuple[SdkDispatchRow, ...],
) -> int | None:
    """Terminal timestamp for the worker leg that can park this root.

    A fresh ``manage.charter.tick.admitted`` binds a new ``worker_thread``; prior
    window terminals must not keep the root parked after re-admit.
    """
    latest: int | None = None
    for row in dispatches:
        if row.root_id != root_id:
            continue
        if worker_thread and row.thread_id and row.thread_id != worker_thread:
            continue
        if row.state in SDK_TERMINAL_STATES and row.terminal_ms:
            if latest is None or row.terminal_ms > latest:
                latest = row.terminal_ms
    return latest


def root_rows(
    fold: CharterFold,
    dispatches: tuple[SdkDispatchRow, ...],
    thresholds: Thresholds,
    now_ms: int,
) -> tuple[CharterRootRow, ...]:
    """Project charter fold state to sorted frozen rows, resolving parked parents.

    ``parked`` is the one genuinely cross-family state, and the reason ``derive``
    exists rather than each fold emitting its own rows: a root is parked when its
    worker leg reached a terminal but no ``root_closed`` was ever observed for the
    root. Neither fold can see that alone, and the join runs on evidence from
    :class:`~dispatch_monitor_core.correlation.CorrelationIndex` -- never on
    timestamp proximity.
    """
    rows = []
    for state in fold.roots.values():
        row_state = state.state
        terminal_ms = _terminal_ms_for_current_worker(
            state.root_id, state.worker_thread, dispatches
        )
        if (
            row_state not in ROOT_CLOSED_STATES
            and not state.closed
            and terminal_ms is not None
            and (age(now_ms, terminal_ms) or 0) >= thresholds.parked_parent_warn_ms
        ):
            row_state = "parked"
        rows.append(
            CharterRootRow(
                root_id=state.root_id,
                state=row_state,
                project=state.project,
                worker_thread=state.worker_thread,
                window_index=state.window_index,
                admission_mode=state.admission_mode,
                packet_path=state.packet_path,
                arc_g_step=state.arc_g_step,
                arc_g_step_label=state.arc_g_step_label,
                pickup_gid=state.pickup_gid,
                objective=state.objective,
                bus_slug=state.bus_slug,
                bus_summary=state.bus_summary,
                last_signal_ms=state.last_signal_ms,
                last_signal=state.last_signal,
                admitted_at_ms=state.admitted_at_ms,
                in_flight_age_ms=(
                    None if state.closed else age(now_ms, state.admitted_at_ms)
                ),
                skip_reason=state.skip_reason,
                skip_streak=state.skip_streak,
                checkpoint_turn=state.checkpoint_turn,
                waiting_open_since_ms=state.waiting_open_since_ms,
                closed=state.closed,
                unenrolled=state.unenrolled,
            )
        )
    rows.sort(key=lambda r: r.root_id)
    return tuple(rows)
