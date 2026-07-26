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
from .dtos import CdpLegRow, CharterRootRow, SdkDispatchRow, Thresholds
from .folds import CdpFold, CharterFold, SdkFold

#: Root states meaning the root's own lifecycle has ended.
ROOT_CLOSED_STATES = ("closed",)

#: Dispatch states meaning the worker leg is over, one way or another.
SDK_TERMINAL_STATES = ("completed", "failed")


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
        rows.append(
            CdpLegRow(
                execution_id=state.execution_id,
                state=state.state,
                picker_model=state.picker_model,
                dispatch_thread_id=state.dispatch_thread_id,
                root_id=state.root_id or index.root_for_cdp(state.execution_id),
                prompt_uri=state.prompt_uri,
                submitted_ms=state.submitted_ms,
                last_progress_ms=state.last_progress_ms,
                terminal_ms=state.terminal_ms,
                idle_age_ms=age(now_ms, state.last_progress_ms) if live else None,
                archive_uri=state.archive_uri,
                content_proof_uri=state.content_proof_uri,
                stall_stage=state.stall_stage,
                failure_reason=state.failure_reason,
                proof_present=bool(state.archive_uri or state.content_proof_uri),
            )
        )
    rows.sort(key=lambda r: r.execution_id)
    return tuple(rows)


def _latest_terminal_by_root(
    dispatches: tuple[SdkDispatchRow, ...],
) -> dict[str, int]:
    """Return each root's latest worker-leg terminal timestamp."""
    latest: dict[str, int] = {}
    for row in dispatches:
        if row.root_id and row.state in SDK_TERMINAL_STATES and row.terminal_ms:
            if row.terminal_ms > latest.get(row.root_id, 0):
                latest[row.root_id] = row.terminal_ms
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
    terminal_by_root = _latest_terminal_by_root(dispatches)
    rows = []
    for state in fold.roots.values():
        row_state = state.state
        terminal_ms = terminal_by_root.get(state.root_id)
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
