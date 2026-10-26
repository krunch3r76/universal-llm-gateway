"""A2a gate, fleet liveness helpers, and maestro reply for deferred recycle."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checks import RefuseFinding, observe_manage_inflight
from .detach import HUB_ROOT, LOG_PATH
from .result import GuardedReexecResult
from .runner import _write_result_file

DEFAULT_POLL_S = 2.0
DEFAULT_WAIT_PARENT_TIMEOUT_S = 900.0
AgentBusReplyFn = Callable[..., None]

RECYCLE_FLEET_SERVICES = (
    "stargate",
    "gateway",
    "rag",
    "agent_bus",
    "event_service",
    "mcp",
    "git_integration_worker",
    "cloud_proxy",
    "cortex_api",
)

_ACTIVE_COUNT_ONLY = re.compile(r"^active_count=\d+$")
ManageCall = Callable[..., dict[str, Any]]


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


def _manage(
    manage_call: ManageCall,
    method: str,
    params: dict[str, Any] | None,
    sock_path: str | None,
) -> dict[str, Any]:
    if sock_path:
        return manage_call(method, params or {}, sock_path=sock_path)
    return manage_call(method, params or {})


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
        holder = row.get("holder")
        if name == "mcp":
            if (row.get("busy") or holder is not None) and not mcp_b:
                return RefuseFinding(
                    reason="step1_mcp_not_exception_b",
                    offenders=[{"service": "mcp", "row": row}],
                )
            continue
        if name == "stargate":
            if holder is not None or (
                row.get("busy")
                and not stargate_row_matches_exception_c(row, mcp_b=mcp_b)
            ):
                return RefuseFinding(
                    reason="step1_stargate_not_exception_c",
                    offenders=[{"service": "stargate", "row": row}],
                )
            continue
        if name == "git_integration_worker":
            det = row.get("determination")
            if (
                row.get("probe_error") is not None
                or det == "undetermined"
                or (det is not None and det != "idle")
            ):
                return RefuseFinding(
                    reason="step1_giw_busy_status_undetermined",
                    offenders=[{"service": name, "row": row}],
                )
            if row.get("busy") or holder is not None:
                return RefuseFinding(
                    reason="step1_giw_agent_bus_busy",
                    offenders=[{"service": name, "row": row}],
                )
            continue
        if name == "agent_bus":
            if row.get("busy") or holder is not None:
                return RefuseFinding(
                    reason="step1_giw_agent_bus_busy",
                    offenders=[{"service": name, "row": row}],
                )
            continue
        if holder is not None:
            return RefuseFinding(
                reason="step1_unclassified_holder",
                offenders=[{"service": name, "row": row}],
            )
    return None


def fleet_liveness_snapshot(
    manage_call: ManageCall,
    sock_path: str | None,
    *,
    code_ref: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "services": list(RECYCLE_FLEET_SERVICES),
        "projection": "compact",
    }
    if code_ref:
        params["code_ref"] = code_ref
    return _manage(manage_call, "fleet_liveness", params, sock_path)


def _container_started_at(row: dict[str, Any]) -> str | None:
    marker = row.get("load_marker")
    if not isinstance(marker, dict):
        return None
    if marker.get("kind") != "container_started_at":
        return None
    if marker.get("error") is not None:
        return None
    value = marker.get("value_utc")
    if isinstance(value, str) and value:
        return value
    return None


def fleet_child_pids(manage_call: ManageCall, sock_path: str | None) -> dict[str, Any]:
    snap = fleet_liveness_snapshot(manage_call, sock_path)
    rows = snap.get("services") if isinstance(snap.get("services"), list) else []
    by_service: dict[str, dict[str, Any]] = {}
    for row in rows:
        if isinstance(row, dict) and row.get("service") is not None:
            by_service[str(row.get("service"))] = row
    return {
        name: {
            "pid": (by_service.get(name) or {}).get("pid"),
            "marker": _container_started_at(by_service.get(name) or {}),
        }
        for name in RECYCLE_FLEET_SERVICES
    }


def _child_record(child: dict[str, Any], name: str) -> dict[str, Any]:
    raw = child.get(name) if isinstance(child, dict) else None
    if isinstance(raw, dict):
        return {"pid": raw.get("pid"), "marker": raw.get("marker")}
    return {"pid": None, "marker": None}


def _usable_marker(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _marker_compare_line(service: str, before: str | None, after: str | None) -> str:
    if before is not None and after is not None:
        relation = "unchanged" if before == after else "changed"
    else:
        relation = "missing"
    before_token = before if before is not None else "missing"
    after_token = after if after is not None else "missing"
    return (
        f"marker_compare: {service} container_started_at "
        f"{before_token} -> {after_token} {relation}"
    )


def evaluate_r3_pass_verdict(
    *,
    target_ref: str,
    result_status: str,
    child_before: dict[str, Any],
    child_after: dict[str, Any],
    manage_call: ManageCall,
    sock_path: str | None,
) -> tuple[str, dict[str, Any]]:
    snap = fleet_liveness_snapshot(manage_call, sock_path, code_ref=target_ref)
    checkout = snap.get("checkout") if isinstance(snap.get("checkout"), dict) else {}
    head_sha = checkout.get("head_sha")
    mcp_auth: str | None = None
    rows = snap.get("services") if isinstance(snap.get("services"), list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("service") == "mcp":
            ve = row.get("version_evidence")
            if isinstance(ve, dict):
                mcp_auth = ve.get("authoritative_for_liveness")
    probe_line = (
        f"services[mcp].version_evidence.authoritative_for_liveness={mcp_auth!r}"
    )
    extra = {
        "probe_39439": probe_line,
        "line_39455": (
            "39455: evidence = step 5; markers api_dispatch.py:761, restart_drain.py:707"
        ),
        "checkout_head_sha": head_sha,
    }
    if result_status != "proof-satisfied":
        return "FAIL", extra
    unverifiable: list[str] = []
    marker_lines: list[str] = []
    saw_fail = False
    saw_indeterminate = False
    for name in RECYCLE_FLEET_SERVICES:
        before = _child_record(child_before, name)
        after = _child_record(child_after, name)
        pid_before = before["pid"]
        pid_after = after["pid"]
        if pid_before is not None and pid_after is not None:
            if pid_before != pid_after:
                saw_fail = True
            continue
        if pid_before is not None or pid_after is not None:
            saw_fail = True
            continue
        mark_before = _usable_marker(before["marker"])
        mark_after = _usable_marker(after["marker"])
        marker_lines.append(_marker_compare_line(name, mark_before, mark_after))
        if mark_before is not None and mark_after is not None:
            if mark_before == mark_after:
                unverifiable.append(name)
            else:
                saw_fail = True
        else:
            saw_indeterminate = True
    if unverifiable:
        extra["pid_unverifiable"] = unverifiable
    if marker_lines:
        extra["marker_compare"] = marker_lines
    if saw_fail:
        return "FAIL", extra
    if head_sha != target_ref:
        return "FAIL", extra
    if mcp_auth != "process_start_code_version":
        return "FAIL", extra
    if saw_indeterminate:
        return "INDETERMINATE", extra
    return "PASS", extra


def post_maestro_reply(
    ctx: DeferredRecycleContext,
    *,
    report_status: str,
    reason: str | None = None,
    manage_before: dict[str, Any],
    manage_after: dict[str, Any] | None,
    child_pids_before: dict[str, Any],
    child_pids_after: dict[str, Any],
    exit_code: int,
    reply_fn: AgentBusReplyFn | None = None,
    body_extra: dict[str, Any] | None = None,
    r3_verdict: str | None = None,
) -> None:
    body_lines = [f"status: {report_status}"]
    if report_status == "executed":
        body_lines.append(
            f"manage_pid: {manage_before.get('pid')} -> {(manage_after or {}).get('pid')}"
        )
        body_lines.append(
            f"code_version: {manage_before.get('code_version')} -> "
            f"{(manage_after or {}).get('code_version')}"
        )
    elif reason:
        body_lines.append(f"reason: {reason}")
    if r3_verdict is not None and report_status == "executed":
        body_lines.append(f"r3_verdict: {r3_verdict}")
    body_lines.extend(
        [
            f"child_pids_before: {child_pids_before}",
            f"child_pids_after: {child_pids_after}",
            f"exit_code: {exit_code}",
            f"log_path: {LOG_PATH}",
            f"result_path: {ctx.result_path}",
        ]
    )
    if body_extra:
        if body_extra.get("probe_39439"):
            body_lines.append(body_extra["probe_39439"])
        if body_extra.get("line_39455"):
            body_lines.append(body_extra["line_39455"])
        unverifiable = body_extra.get("pid_unverifiable") or []
        if unverifiable:
            body_lines.append(
                "pid_unverifiable: " + ", ".join(str(name) for name in unverifiable)
            )
        for line in body_extra.get("marker_compare") or []:
            body_lines.append(str(line))
    body = "\n".join(body_lines)
    subject = f"DONE|decision:manage-recycle-39439-39455-r3b status={report_status}"
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
        report_status="refused",
        reason=str(exc),
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=1,
        reply_fn=reply_fn,
    )
