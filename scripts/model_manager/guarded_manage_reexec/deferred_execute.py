"""Deferred manage recycle — wait, re-gate, execute."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import RefuseFinding, observe_manage_inflight
from .client import call_manage
from .detach import HUB_ROOT, LOG_PATH
from .result import GuardedReexecResult
from .runner import _write_result_file, run_guarded_reexec

RECYCLE_FLEET_SERVICES = (
    "stargate", "gateway", "rag", "agent_bus", "event_service",
    "mcp", "git_integration_worker", "cloud_proxy", "cortex_api",
)

_STATUS_TERMINAL = frozenset({"completed", "failed", "cancelled"})
DEFAULT_POLL_S = 2.0
DEFAULT_WAIT_PARENT_TIMEOUT_S = 900.0
_ACTIVE_COUNT_ONLY = re.compile(r"^active_count=\d+$")

ManageCall = Callable[..., dict[str, Any]]
DispatchStatusFn = Callable[[str], dict[str, Any] | None]
GiwIdleFn = Callable[[], bool]
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


def giw_idle_from_ledger(ledger: Any) -> bool:
    snap = ledger.lease_snapshot()
    if snap.get("holder_dispatch_id"):
        return False
    if int(snap.get("queue_depth") or 0) > 0:
        return False
    active = ledger.active_snapshot()
    return int(active.get("running") or 0) == 0


def mcp_row_matches_exception_b(row: dict[str, Any]) -> bool:
    holder = row.get("holder")
    if not isinstance(holder, dict):
        return False
    return (
        holder.get("kind") == "mcp_life_session"
        and holder.get("subject_preview") == "cdp_ask running=1"
        and holder.get("dispatch_id") is None
    )


def stargate_row_matches_exception_c(
    stargate_row: dict[str, Any], *, mcp_b: bool
) -> bool:
    if not mcp_b:
        return False
    if stargate_row.get("holder") is not None:
        return False
    if stargate_row.get("probe_error") is not None:
        return False
    summary = str(stargate_row.get("active_work_summary") or "")
    return bool(_ACTIVE_COUNT_ONLY.fullmatch(summary))


def _giw_agent_bus_harmful(row: dict[str, Any]) -> bool:
    return bool(row.get("busy"))


def run_recycle_step1_per_service_gate(
    manage_call: ManageCall,
    *,
    parent_dispatch_id: str,
    sock_path: str | None = None,
) -> RefuseFinding | None:
    del parent_dispatch_id  # (a) inert at child gate — parent id is not a pass.
    rows: dict[str, dict[str, Any]] = {}
    for name in RECYCLE_FLEET_SERVICES:
        payload = _manage(manage_call, "busy_status", {"service": name}, sock_path)
        if payload.get("status") == "error":
            return RefuseFinding(
                reason="busy_status_unobservable",
                offenders=[{"service": name, "payload": payload}],
            )
        rows[name] = payload

    for name, row in rows.items():
        if row.get("restart_intent") is not None:
            return RefuseFinding(
                reason="nonterminal_restart_intent",
                offenders=[{"service": name, "service_row": row}],
            )
        if row.get("restart_window") is not None:
            return RefuseFinding(
                reason="open_restart_window",
                offenders=[{"service": name, "service_row": row}],
            )
        if "process" in row:
            finding, _, _, _ = observe_manage_inflight(row)
            if finding is not None:
                finding.offenders[0]["service"] = name
                return finding

    mcp_row = rows["mcp"]
    mcp_b = mcp_row_matches_exception_b(mcp_row)
    for name, row in rows.items():
        if name == "mcp":
            if row.get("busy") and not mcp_b:
                return RefuseFinding(
                    reason="step1_mcp_not_exception_b",
                    offenders=[{"service": "mcp", "row": row}],
                )
            continue
        if name == "stargate":
            if row.get("busy") and not stargate_row_matches_exception_c(
                row, mcp_b=mcp_b
            ):
                return RefuseFinding(
                    reason="step1_stargate_not_exception_c",
                    offenders=[{"service": "stargate", "row": row}],
                )
            continue
        if name in ("git_integration_worker", "agent_bus"):
            if _giw_agent_bus_harmful(row):
                return RefuseFinding(
                    reason="step1_giw_agent_bus_busy",
                    offenders=[{"service": name, "row": row}],
                )
            continue
        if row.get("busy") and row.get("holder") is not None:
            return RefuseFinding(
                reason="step1_unclassified_holder",
                offenders=[{"service": name, "row": row}],
            )
    return None


def wait_for_parent_and_giw_idle(
    *,
    parent_dispatch_id: str,
    dispatch_status_fn: DispatchStatusFn,
    giw_idle_fn: GiwIdleFn,
    timeout_s: float,
    poll_s: float,
    clock: ClockFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> bool:
    now = clock or time.time
    sleep = sleep_fn or time.sleep
    deadline = now() + timeout_s
    while now() < deadline:
        parent_row = dispatch_status_fn(parent_dispatch_id)
        if dispatch_status_terminal(parent_row) and giw_idle_fn():
            return True
        sleep(poll_s)
    return False


def _fleet_child_pids(manage_call: ManageCall, sock_path: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in RECYCLE_FLEET_SERVICES:
        health = _manage(manage_call, "health", {"service": name}, sock_path)
        out[name] = health.get("pid")
    return out


def _verdict_from_result(result: GuardedReexecResult) -> str:
    if result.status == "proof-satisfied":
        return "PASS"
    if result.status in ("refused", "proof-failed", "start-failed"):
        return "FAIL"
    return "INDETERMINATE"


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
) -> None:
    body_lines = [
        f"VERDICT: {verdict}",
        f"manage_pid: {manage_before.get('pid')} -> {(manage_after or {}).get('pid')}",
        f"code_version: {manage_before.get('code_version')} -> {(manage_after or {}).get('code_version')}",
        f"child_pids_before: {child_pids_before}",
        f"child_pids_after: {child_pids_after}",
        f"exit_code: {exit_code}",
        f"log_path: {LOG_PATH}",
        f"result_path: {ctx.result_path}",
    ]
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
    subprocess.run(
        [
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
    )


def _ledger_factory_default() -> Any:
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance()


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
        child_pids_after=child_before,
        exit_code=2,
        reply_fn=reply_fn,
    )
    return 2, refused


def run_deferred_recycle(
    ctx: DeferredRecycleContext,
    *,
    manage_call: ManageCall | None = None,
    dispatch_status_fn: DispatchStatusFn | None = None,
    giw_idle_fn: GiwIdleFn | None = None,
    ledger_factory: Callable[[], Any] | None = None,
    reply_fn: AgentBusReplyFn | None = None,
    clock: ClockFn | None = None,
    sleep_fn: SleepFn | None = None,
) -> tuple[int, GuardedReexecResult]:
    manage = manage_call or call_manage
    sock = ctx.sock_path

    manage_before = _manage(manage, "whoami", {}, sock)
    child_before = _fleet_child_pids(manage, sock)

    factory = ledger_factory or _ledger_factory_default
    if dispatch_status_fn is None:
        dispatch_status_fn = lambda did: factory().dispatch_status_by_id(  # noqa: E731
            dispatch_id=did
        )
    if giw_idle_fn is None:
        giw_idle_fn = lambda: giw_idle_from_ledger(factory())  # noqa: E731

    ready = wait_for_parent_and_giw_idle(
        parent_dispatch_id=ctx.parent_dispatch_id,
        dispatch_status_fn=dispatch_status_fn,
        giw_idle_fn=giw_idle_fn,
        timeout_s=ctx.wait_timeout_s,
        poll_s=ctx.poll_s,
        clock=clock,
        sleep_fn=sleep_fn,
    )

    if not ready:
        return _refused_exit(
            ctx,
            reason="parent_wait_timeout",
            manage_before=manage_before,
            child_before=child_before,
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
            manage_before=manage_before,
            child_before=child_before,
            reply_fn=reply_fn,
            checks={"findings": [{"reason": gate.reason, "offenders": gate.offenders}]},
        )

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
    exit_code = 0 if result.status == "proof-satisfied" else 2
    if result.status == "crashed":
        exit_code = 1
    elif result.status == "proof-failed":
        exit_code = 3
    elif result.status == "start-failed":
        exit_code = 4
    post_maestro_reply(
        ctx,
        verdict=_verdict_from_result(result),
        manage_before=manage_before,
        manage_after=manage_after,
        child_pids_before=child_before,
        child_pids_after=child_after,
        exit_code=exit_code,
        reply_fn=reply_fn,
    )
    return exit_code, result
