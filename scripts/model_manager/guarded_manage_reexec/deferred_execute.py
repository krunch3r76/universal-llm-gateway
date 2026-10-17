"""Deferred manage recycle — wait, re-gate, execute, maestro reply."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
)

from .client import call_manage
from .deferred_gate import RECYCLE_FLEET_SERVICES, run_recycle_step1_per_service_gate
from .detach import HUB_ROOT, LOG_PATH
from .result import GuardedReexecResult
from .runner import _write_result_file, run_guarded_reexec

_STATUS_TERMINAL = frozenset({"completed", "failed", "cancelled"})
DEFAULT_POLL_S = 2.0
DEFAULT_WAIT_PARENT_TIMEOUT_S = 900.0

ManageCall = Callable[..., dict[str, Any]]
DispatchStatusFn = Callable[[str], dict[str, Any] | None]
GiwIdleProbeFn = Callable[[], tuple[bool, str | None]]
ClockFn = Callable[[], float]
SleepFn = Callable[[float], None]
AgentBusReplyFn = Callable[..., None]


def _manage(
    manage_call: ManageCall,
    method: str,
    params: dict[str, Any] | None,
    sock_path: str | None,
) -> dict[str, Any]:
    if sock_path:
        return manage_call(method, params or {}, sock_path=sock_path)
    return manage_call(method, params or {})


@dataclass(slots=True)
class DeferredRecycleContext:
    parent_dispatch_id: str
    report_thread: str
    report_after_turn: int
    target_ref: str
    tmux_target: str
    result_path: Path
    wait_timeout_s: float = DEFAULT_WAIT_PARENT_TIMEOUT_S
    poll_s: float = DEFAULT_POLL_S
    repo_root: Path = HUB_ROOT
    sock_path: str | None = None


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
    """Return (idle_ok, refuse_reason). refuse_reason forces immediate fail-closed refuse."""
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
    """Return (ready, refuse_reason). refuse_reason is fail-closed (not timeout)."""
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


def _fleet_liveness_snapshot(
    manage_call: ManageCall, sock_path: str | None
) -> dict[str, Any]:
    return _manage(
        manage_call,
        "fleet_liveness",
        {"services": list(RECYCLE_FLEET_SERVICES), "projection": "compact"},
        sock_path,
    )


def _fleet_child_pids(manage_call: ManageCall, sock_path: str | None) -> dict[str, Any]:
    snap = _fleet_liveness_snapshot(manage_call, sock_path)
    rows = snap.get("services") if isinstance(snap.get("services"), list) else []
    by_service = {
        str(row.get("service")): row.get("pid")
        for row in rows
        if isinstance(row, dict)
    }
    return {name: by_service.get(name) for name in RECYCLE_FLEET_SERVICES}


def _mcp_authoritative_liveness(snap: dict[str, Any]) -> str | None:
    rows = snap.get("services") if isinstance(snap.get("services"), list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("service") == "mcp":
            ve = row.get("version_evidence")
            if isinstance(ve, dict):
                return ve.get("authoritative_for_liveness")
    return None


def _verdict_after_execute(
    ctx: DeferredRecycleContext,
    result: GuardedReexecResult,
    *,
    manage_call: ManageCall,
    child_before: dict[str, Any],
    child_after: dict[str, Any],
    sock_path: str | None,
) -> tuple[str, dict[str, Any]]:
    snap = _fleet_liveness_snapshot(manage_call, sock_path)
    checkout = snap.get("checkout") if isinstance(snap.get("checkout"), dict) else {}
    head_sha = checkout.get("head_sha")
    mcp_auth = _mcp_authoritative_liveness(snap)
    probe_line = (
        'services[mcp].version_evidence.authoritative_for_liveness='
        f"{mcp_auth!r}"
    )
    extra = {
        "probe_39439": probe_line,
        "line_39455": (
            "39455: evidence = step 5; markers api_dispatch.py:761, restart_drain.py:707"
        ),
        "checkout_head_sha": head_sha,
    }
    if result.status != "proof-satisfied":
        return "FAIL", extra
    if child_before != child_after:
        return "FAIL", extra
    if head_sha != ctx.target_ref:
        return "FAIL", extra
    if mcp_auth != "process_start_code_version":
        return "FAIL", extra
    return "PASS", extra


def post_maestro_reply(
    ctx: DeferredRecycleContext,
    *,
    verdict: str,
    manage_before: dict[str, Any],
    manage_after: dict[str, Any] | None,
    child_pids_before: dict[str, Any],
    child_pids_after: dict[str, Any],
    exit_code: int,
    reply_fn: AgentBusReplyFn | None = None,
    body_extra: dict[str, Any] | None = None,
) -> None:
    body_lines = [
        f"VERDICT: {verdict}",
        f"manage_pid: {manage_before.get('pid')} -> {(manage_after or {}).get('pid')}",
        f"code_version: {manage_before.get('code_version')} -> "
        f"{(manage_after or {}).get('code_version')}",
        f"child_pids_before: {child_pids_before}",
        f"child_pids_after: {child_pids_after}",
        f"exit_code: {exit_code}",
        f"log_path: {LOG_PATH}",
        f"result_path: {ctx.result_path}",
    ]
    if body_extra:
        if body_extra.get("probe_39439"):
            body_lines.append(body_extra["probe_39439"])
        if body_extra.get("line_39455"):
            body_lines.append(body_extra["line_39455"])
    body = "\n".join(body_lines)
    subject = f"DONE|decision:manage-recycle-39439-39455-r3b VERDICT: {verdict}"
    if reply_fn is not None:
        reply_fn(
            thread=ctx.report_thread,
            to="maestro",
            subject=subject,
            body=body,
            after_turn=ctx.report_after_turn,
        )
        return
    script = ctx.repo_root / "scripts" / "agent-bus"
    proc = subprocess.run(
        [
            sys.executable,
            str(script),
            "reply",
            "--thread",
            str(ctx.report_thread),
            "--to",
            "maestro",
            "--subject",
            subject,
            "--body",
            body,
            "--after-turn",
            str(ctx.report_after_turn),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    print(
        f"agent-bus reply exit={proc.returncode}",
        file=sys.stderr,
        flush=True,
    )


def report_deferred_crash(
    ctx: DeferredRecycleContext,
    exc: BaseException,
    tb: str,
    *,
    reply_fn: AgentBusReplyFn | None = None,
) -> None:
    crashed = GuardedReexecResult(
        status="crashed",
        reason=str(exc),
        dry_run=False,
        target_ref=ctx.target_ref,
        executed=False,
        traceback=tb,
    )
    _write_result_file(ctx.result_path, crashed)
    post_maestro_reply(
        ctx,
        verdict="FAIL",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=1,
        reply_fn=reply_fn,
    )


def _refused_exit(
    ctx: DeferredRecycleContext,
    *,
    reason: str,
    manage_before: dict[str, Any],
    child_before: dict[str, Any],
    reply_fn: AgentBusReplyFn | None,
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
    reply_fn: AgentBusReplyFn | None = None,
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
    child_before = _fleet_child_pids(manage, sock)

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
    child_after = _fleet_child_pids(manage, sock)
    verdict, body_extra = _verdict_after_execute(
        ctx,
        result,
        manage_call=manage,
        child_before=child_before,
        child_after=child_after,
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
