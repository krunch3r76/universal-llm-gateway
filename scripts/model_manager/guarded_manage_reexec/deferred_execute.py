"""Deferred manage recycle — wait, re-gate, execute orchestration."""

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
    if not row:
        return False
    return str(row.get("status") or "") in _STATUS_TERMINAL


def giw_idle_probe(
    manage_call: ManageCall,
    ledger_factory: Callable[[], Any],
    *,
    sock_path: str | None,
) -> tuple[bool, str | None]:
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
    if row.get("busy") or row.get("holder") is not None:
        return False, None
    return True, None


def _ledger_factory_default() -> CursorDispatchLedger:
    return CursorDispatchLedger.instance()


def wait_for_parent_and_giw_idle(
    *,
    parent_dispatch_id: str,
    dispatch_status_fn: DispatchStatusFn,
    giw_idle_probe_fn: GiwIdleProbeFn,
    timeout_s: float,
    poll_s: float,
    clock: ClockFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> tuple[bool, str | None]:
    now = clock or time.time
    sleep = sleep_fn or time.sleep
    deadline = now() + timeout_s
    while now() < deadline:
        idle, refuse = giw_idle_probe_fn()
        if refuse:
            return False, refuse
        parent_row = dispatch_status_fn(parent_dispatch_id)
        if dispatch_status_terminal(parent_row) and idle:
            return True, None
        sleep(poll_s)
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
        verdict="FAIL",
        manage_before=manage_before,
        manage_after=None,
        child_pids_before=child_before,
        child_pids_after={},
        exit_code=2,
        reply_fn=reply_fn,
    )
    return 2, refused


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
    manage = manage_call or call_manage
    sock = ctx.sock_path
    factory = ledger_factory or _ledger_factory_default

    if dispatch_status_fn is None:
        dispatch_status_fn = lambda did: factory().dispatch_status_by_id(  # noqa: E731
            dispatch_id=did
        )

    giw_probe = giw_idle_probe_fn or (
        lambda: giw_idle_probe(manage, factory, sock_path=sock)  # noqa: E731
    )

    ready, refuse_reason = wait_for_parent_and_giw_idle(
        parent_dispatch_id=ctx.parent_dispatch_id,
        dispatch_status_fn=dispatch_status_fn,
        giw_idle_probe_fn=giw_probe,
        timeout_s=ctx.wait_timeout_s,
        poll_s=ctx.poll_s,
        clock=clock,
        sleep_fn=sleep_fn,
    )

    if refuse_reason:
        return _refused_exit(
            ctx,
            reason=refuse_reason,
            manage_before={},
            child_before={},
            reply_fn=reply_fn,
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
    if gate is not None:
        return _refused_exit(
            ctx,
            reason=gate.reason,
            manage_before={},
            child_before={},
            reply_fn=reply_fn,
            checks={"findings": [{"reason": gate.reason, "offenders": gate.offenders}]},
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
    post_maestro_reply(
        ctx,
        verdict=verdict,
        manage_before=manage_before,
        manage_after=manage_after,
        child_pids_before=child_before,
        child_pids_after=child_after,
        exit_code=exit_code,
        reply_fn=reply_fn,
        body_extra=body_extra,
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
