"""Deferred manage recycle — one deadline for parent wait, GIW idle, and step1.

``run_deferred_recycle`` is the child path after a parent dispatch is armed.
It polls until that parent row is terminal and git_integration_worker is idle,
re-checks the step1 gate, then calls ``run_guarded_reexec``. Transient GIW
probe failures stay inside the original timeout instead of ending the window.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

from .client import call_manage
from .deferred_gate import (
    DEFAULT_POLL_S,
    DEFAULT_WAIT_PARENT_TIMEOUT_S,
    DeferredRecycleContext,
    evaluate_r3_pass_verdict,
    fleet_child_pids,
    post_maestro_reply,
    report_deferred_crash,
    run_recycle_step1_per_service_gate,
)
from .result import GuardedReexecResult
from .runner import _write_result_file, run_guarded_reexec

_STATUS_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_GIW_IDLE_TRANSIENT_REASONS = frozenset(
    {
        "giw_idle_ledger_unreadable",
        "giw_idle_busy_status_unobservable",
        "giw_idle_busy_status_undetermined",
    }
)
_CHECK_TRANSIENT_CONSECUTIVE = "giw_idle_transient_consecutive"
_CHECK_TRANSIENT_TOTAL = "giw_idle_transient_total"

ManageCall = Callable[..., dict[str, Any]]
DispatchStatusFn = Callable[[str], dict[str, Any] | None]
GiwIdleProbeFn = Callable[[], tuple[bool, str | None]]
ClockFn = Callable[[], float]
SleepFn = Callable[[float], None]


def _manage(
    manage_call: ManageCall,
    method: str,
    params: dict[str, Any] | None,
    sock_path: str | None,
) -> dict[str, Any]:
    if sock_path:
        return manage_call(method, params or {}, sock_path=sock_path)
    return manage_call(method, params or {})


def dispatch_status_terminal(row: dict[str, Any] | None) -> bool:
    """True when a dispatch row has reached completed, failed, or cancelled.

    A missing row is not terminal. Callers use this to decide whether the
    parent dispatch has left the in-flight set. No I/O.
    """
    if not row:
        return False
    return str(row.get("status") or "") in _STATUS_TERMINAL


def giw_idle_probe(
    manage_call: ManageCall,
    ledger_factory: Callable[[], Any],
    *,
    sock_path: str | None,
) -> tuple[bool, str | None]:
    """Read the GIW lease and busy_status once and say whether it is idle.

    Returns ``(True, None)`` only when the lease holder is empty, the queue
    depth is zero, and busy_status determination is idle with no holder.
    Ledger read failures, unobservable busy_status, and undetermined rows
    return a named refuse reason. Busy or held rows return ``(False, None)``.
    Side effect: one ``busy_status`` manage call for git_integration_worker.
    """
    try:
        snap = ledger_factory().lease_snapshot()
    except Exception:
        return False, "giw_idle_ledger_unreadable"
    if snap.get("holder_dispatch_id"):
        return False, None
    if int(snap.get("queue_depth") or 0) > 0:
        return False, None
    row = _manage(
        manage_call,
        "busy_status",
        {"service": "git_integration_worker"},
        sock_path,
    )
    if row.get("status") == "error":
        return False, "giw_idle_busy_status_unobservable"
    if row.get("probe_error") is not None or row.get("determination") == "undetermined":
        return False, "giw_idle_busy_status_undetermined"
    if row.get("busy") or row.get("holder") is not None:
        return False, None
    if row.get("determination") != "idle":
        return False, None
    return True, None


def _ledger_factory_default() -> CursorDispatchLedger:
    return CursorDispatchLedger.instance()


def _bounded_sleep(
    sleep: SleepFn,
    *,
    poll_s: float,
    deadline: float,
    now: ClockFn,
) -> None:
    remaining = deadline - now()
    if remaining <= 0:
        return
    sleep(min(poll_s, remaining))


def wait_for_parent_and_giw_idle(
    *,
    parent_dispatch_id: str,
    dispatch_status_fn: DispatchStatusFn,
    giw_idle_probe_fn: GiwIdleProbeFn,
    timeout_s: float,
    poll_s: float,
    clock: ClockFn | None = None,
    sleep_fn: SleepFn | None = None,
    checks: dict[str, Any] | None = None,
) -> tuple[bool, str | None]:
    """Poll until the parent is terminal and GIW is idle, or the budget ends.

    ``timeout_s`` is the remaining seconds from ``clock()`` for this call.
    Callers that already own a deadline pass only the time left; this function
    does not add a fresh full window. Ledger-unreadable, busy-status
    unobservable, and busy-status undetermined samples count as not idle and
    keep polling. At the deadline a trailing transient reason is returned and
    consecutive plus total counts are written onto ``checks`` when that dict
    is provided. Any other exhausted budget returns ``(False, None)``. A
    refuse string outside that set returns immediately.

    Returns ``(True, None)`` when the parent row is terminal and the probe is
    idle. No I/O beyond the injected probe and status callables.
    """
    now = clock or time.time
    sleep = sleep_fn or time.sleep
    deadline = now() + timeout_s
    consecutive = 0
    total = 0
    last_transient: str | None = None
    while now() < deadline:
        idle, refuse = giw_idle_probe_fn()
        if refuse in _GIW_IDLE_TRANSIENT_REASONS:
            last_transient = refuse
            consecutive += 1
            total += 1
        elif refuse:
            return False, refuse
        else:
            consecutive = 0
            last_transient = None
            parent_row = dispatch_status_fn(parent_dispatch_id)
            if dispatch_status_terminal(parent_row) and idle:
                return True, None
        _bounded_sleep(sleep, poll_s=poll_s, deadline=deadline, now=now)
    if last_transient is not None:
        if checks is not None:
            checks[_CHECK_TRANSIENT_CONSECUTIVE] = consecutive
            checks[_CHECK_TRANSIENT_TOTAL] = total
        return False, last_transient
    return False, None


def _refused_exit(
    ctx: DeferredRecycleContext,
    *,
    reason: str,
    manage_before: dict[str, Any],
    child_before: dict[str, Any],
    reply_fn: Callable[..., None] | None,
    checks: dict[str, Any] | None = None,
) -> tuple[int, GuardedReexecResult]:
    refused = GuardedReexecResult(
        status="refused",
        reason=reason,
        dry_run=False,
        target_ref=ctx.target_ref,
        executed=False,
        whoami_before=manage_before,
        checks=checks or {},
    )
    _write_result_file(ctx.result_path, refused)
    post_maestro_reply(
        ctx,
        report_status="refused",
        reason=reason,
        manage_before=manage_before,
        manage_after=None,
        child_pids_before=child_before,
        child_pids_after={},
        exit_code=2,
        reply_fn=reply_fn,
    )
    return 2, refused


def _step1_stargate_busy_reentry(gate: Any) -> bool:
    """True when step1 failed because stargate is busy outside exception C.

    The detached child often reaches step1 only after the parent's stargate
    restart_intent has completed and new active work has started. Re-enter the
    bounded wait and re-check on the next idle window instead of refusing.
    """
    return getattr(gate, "reason", None) == "step1_stargate_not_exception_c"


def _step1_giw_undetermined_reentry(gate: Any) -> bool:
    """True when step1 failed because the GIW row itself is undetermined.

    ``step1_giw_busy_status_undetermined`` also covers busy, dead_heartbeat,
    and in_progress. Only ``probe_error`` or ``determination == "undetermined"``
    on the git_integration_worker offender may re-enter the wait.
    """
    if getattr(gate, "reason", None) != "step1_giw_busy_status_undetermined":
        return False
    for offender in getattr(gate, "offenders", None) or []:
        if not isinstance(offender, dict):
            continue
        if offender.get("service") != "git_integration_worker":
            continue
        row = offender.get("row")
        if not isinstance(row, dict):
            return False
        return (
            row.get("probe_error") is not None
            or row.get("determination") == "undetermined"
        )
    return False


def _gate_finding_checks(gate: Any) -> dict[str, Any]:
    return {
        "findings": [{"reason": gate.reason, "offenders": gate.offenders}],
    }


def run_deferred_recycle(
    ctx: DeferredRecycleContext,
    *,
    manage_call: ManageCall | None = None,
    dispatch_status_fn: DispatchStatusFn | None = None,
    ledger_factory: Callable[[], Any] | None = None,
    giw_idle_probe_fn: GiwIdleProbeFn | None = None,
    reply_fn: Callable[..., None] | None = None,
    clock: ClockFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> tuple[int, GuardedReexecResult]:
    """Wait out the parent, re-gate step1, then execute the guarded recycle.

    The deadline is ``clock() + ctx.wait_timeout_s`` once, before any poll.
    A GIW row with ``probe_error`` set or ``determination == "undetermined"``
    sleeps ``poll_s`` (capped at the time left) and calls the wait again with
    only the remaining seconds. Busy, dead_heartbeat, in_progress, and every
    other step1 reason return immediately. Execute runs only after a terminal
    parent, an idle GIW probe, and a passing step1 gate.

    Returns the process exit code and the guarded result. Refusals write the
    result file and post one maestro reply.
    """
    manage = manage_call or call_manage
    sock = ctx.sock_path
    factory = ledger_factory or _ledger_factory_default
    now = clock or time.time
    sleep = sleep_fn or time.sleep

    if dispatch_status_fn is None:
        dispatch_status_fn = lambda did: factory().dispatch_status_by_id(  # noqa: E731
            dispatch_id=did
        )

    giw_probe = giw_idle_probe_fn or (
        lambda: giw_idle_probe(manage, factory, sock_path=sock)  # noqa: E731
    )

    deadline = now() + ctx.wait_timeout_s
    probe_checks: dict[str, Any] = {}
    deadline_reason = "parent_wait_timeout"
    deadline_checks: dict[str, Any] = {}

    while True:
        remaining = deadline - now()
        if remaining <= 0:
            return _refused_exit(
                ctx,
                reason=deadline_reason,
                manage_before={},
                child_before={},
                reply_fn=reply_fn,
                checks=deadline_checks,
            )
        ready, refuse_reason = wait_for_parent_and_giw_idle(
            parent_dispatch_id=ctx.parent_dispatch_id,
            dispatch_status_fn=dispatch_status_fn,
            giw_idle_probe_fn=giw_probe,
            timeout_s=remaining,
            poll_s=ctx.poll_s,
            clock=clock,
            sleep_fn=sleep_fn,
            checks=probe_checks,
        )
        if refuse_reason:
            return _refused_exit(
                ctx,
                reason=refuse_reason,
                manage_before={},
                child_before={},
                reply_fn=reply_fn,
                checks=probe_checks,
            )
        if not ready:
            return _refused_exit(
                ctx,
                reason="parent_wait_timeout",
                manage_before={},
                child_before={},
                reply_fn=reply_fn,
            )

        gate = run_recycle_step1_per_service_gate(
            manage,
            parent_dispatch_id=ctx.parent_dispatch_id,
            sock_path=sock,
        )
        if gate is None:
            break
        if _step1_giw_undetermined_reentry(gate) or _step1_stargate_busy_reentry(gate):
            deadline_reason = gate.reason
            deadline_checks = _gate_finding_checks(gate)
            remaining = deadline - now()
            if remaining <= 0:
                return _refused_exit(
                    ctx,
                    reason=gate.reason,
                    manage_before={},
                    child_before={},
                    reply_fn=reply_fn,
                    checks=deadline_checks,
                )
            _bounded_sleep(sleep, poll_s=ctx.poll_s, deadline=deadline, now=now)
            continue
        return _refused_exit(
            ctx,
            reason=gate.reason,
            manage_before={},
            child_before={},
            reply_fn=reply_fn,
            checks=_gate_finding_checks(gate),
        )

    manage_before = _manage(manage, "whoami", {}, sock)
    child_before = fleet_child_pids(manage, sock)

    reexec_kwargs: dict[str, Any] = {
        "target_ref": ctx.target_ref,
        "dry_run": False,
        "recover": True,
        "tmux_target": ctx.tmux_target,
        "repo_root": ctx.repo_root,
        "manage_call": manage,
        "result_path": ctx.result_path,
    }
    if sock:
        reexec_kwargs["sock_path"] = sock
    result = run_guarded_reexec(**reexec_kwargs)
    manage_after = _manage(manage, "whoami", {}, sock)
    child_after = fleet_child_pids(manage, sock)
    verdict, body_extra = evaluate_r3_pass_verdict(
        target_ref=ctx.target_ref,
        result_status=result.status,
        child_before=child_before,
        child_after=child_after,
        manage_call=manage,
        sock_path=sock,
    )
    exit_code = 0 if verdict == "PASS" else 2
    if result.status == "crashed":
        exit_code = 1
    elif result.status == "proof-failed":
        exit_code = 3
    elif result.status == "start-failed":
        exit_code = 4
    if verdict == "PASS" and result.status == "proof-satisfied":
        report_status = "executed"
        report_reason: str | None = None
    else:
        report_status = "refused"
        report_reason = result.reason or verdict
    post_maestro_reply(
        ctx,
        report_status=report_status,
        reason=report_reason,
        manage_before=manage_before,
        manage_after=manage_after,
        child_pids_before=child_before,
        child_pids_after=child_after,
        exit_code=exit_code,
        reply_fn=reply_fn,
        body_extra=body_extra,
        r3_verdict=verdict if report_status == "executed" else None,
    )
    return exit_code, result


# Stable import path for tests and __main__ consumers.
__all__ = [
    "DEFAULT_POLL_S",
    "DEFAULT_WAIT_PARENT_TIMEOUT_S",
    "DeferredRecycleContext",
    "dispatch_status_terminal",
    "giw_idle_probe",
    "report_deferred_crash",
    "run_deferred_recycle",
    "wait_for_parent_and_giw_idle",
]
