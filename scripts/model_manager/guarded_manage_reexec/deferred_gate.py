"""A2a gate, fleet liveness helpers, and maestro reply for deferred recycle."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checks import RefuseFinding, observe_manage_inflight
from .detach import HUB_ROOT, LOG_PATH
from .result import GuardedReexecResult
from .runner import _write_result_file

DEFAULT_POLL_S = 2.0
DEFAULT_WAIT_PARENT_TIMEOUT_S = 900.0
REPLY_TIMEOUT_S = 10.0
_MAX_REPLY_POSTS = 3
_LOG_FIELD_CAP = 512
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


def persist_result_projection(result: GuardedReexecResult, path: Path) -> None:
    """Rewrite the result file atomically after ``report`` is attached."""
    try:
        payload = json.dumps(result.as_dict(), indent=2, sort_keys=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _cap_log_field(text: str) -> str:
    if "Bearer " in text or "AGENT_BUS_TOKEN" in text:
        return "[redacted]"
    return text[:_LOG_FIELD_CAP]


def _report_record(
    *,
    outcome: str,
    attempts: int,
    exit_code: int | None,
    cause: str | None,
    stderr_head: str,
    turn_number: int | None,
) -> dict[str, Any]:
    return {
        "outcome": outcome,
        "attempts": attempts,
        "exit_code": exit_code,
        "cause": cause,
        "stderr_head": _cap_log_field(stderr_head),
        "turn_number": turn_number,
        "at": datetime.now(UTC).isoformat(),
    }


def _log_reply_failure(
    *,
    exit_code: int,
    attempt: int,
    thread: str,
    stderr: str,
    stdout: str,
) -> None:
    print(
        f"agent-bus reply exit={exit_code} attempt={attempt} thread={thread} "
        f"stderr={_cap_log_field(stderr)!r} stdout={_cap_log_field(stdout)!r}",
        file=sys.stderr,
        flush=True,
    )


def _log_reply_success(turn_number: int | None) -> None:
    print(f"agent-bus reply delivered turn_number={turn_number}", file=sys.stderr, flush=True)


def _parse_fetch_turns(stdout: str) -> list[dict[str, Any]] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    turns = payload.get("turns")
    if not isinstance(turns, list):
        return None
    return [t for t in turns if isinstance(t, dict)]


def _parse_reply_turn(stdout: str) -> int | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    num = payload.get("turn_number")
    return int(num) if isinstance(num, int) else None


def _classify_reply_stderr(stderr: str, *, timed_out: bool) -> str:
    if "409 Conflict" in stderr:
        return "409"
    if timed_out or "timed out" in stderr or "TimeoutExpired" in stderr:
        return "timeout"
    if any(
        token in stderr
        for token in ("[Errno 2]", "[Errno 111]", "Connection refused")
    ):
        return "request_not_sent"
    if "Server error '5" in stderr:
        return "server_error"
    return "other"


def _readback_match(
    turns: list[dict[str, Any]],
    *,
    high_water: int,
    subject: str,
) -> int | None:
    for row in turns:
        num = row.get("turn_number")
        if not isinstance(num, int) or num <= high_water:
            continue
        if row.get("from") != "cursor" or row.get("to") != "maestro":
            continue
        if row.get("subject") != subject:
            continue
        return num
    return None


def _run_agent_bus_cli(
    ctx: DeferredRecycleContext,
    argv: list[str],
) -> tuple[int, str, str]:
    script = ctx.repo_root / "scripts" / "agent-bus"
    proc = subprocess.run(
        [sys.executable, str(script), *argv],
        check=False,
        capture_output=True,
        text=True,
        timeout=REPLY_TIMEOUT_S,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _fetch_turns(
    ctx: DeferredRecycleContext,
    *,
    last: int,
) -> tuple[list[dict[str, Any]] | None, int, str, str]:
    code, out, err = _run_agent_bus_cli(
        ctx,
        ["fetch", "--thread", str(ctx.report_thread), "--last", str(last)],
    )
    if code != 0:
        return None, code, out, err
    return _parse_fetch_turns(out), code, out, err


def _latest_turn_number(ctx: DeferredRecycleContext) -> tuple[int | None, int, str, str]:
    turns, code, out, err = _fetch_turns(ctx, last=1)
    if turns is None:
        return None, code, out, err
    if not turns:
        return 0, code, out, err
    num = turns[0].get("turn_number")
    return (int(num) if isinstance(num, int) else 0), code, out, err


def _deliver_via_reply_fn(
    ctx: DeferredRecycleContext,
    *,
    reply_fn: AgentBusReplyFn,
    subject: str,
    body: str,
) -> dict[str, Any]:
    try:
        reply_fn(
            thread=ctx.report_thread,
            to="maestro",
            subject=subject,
            body=body,
            after_turn=ctx.report_after_turn,
        )
    except Exception as exc:
        return _report_record(
            outcome="failed",
            attempts=1,
            exit_code=None,
            cause=f"{type(exc).__name__}: {exc}",
            stderr_head="",
            turn_number=None,
        )
    return _report_record(
        outcome="delivered",
        attempts=1,
        exit_code=0,
        cause=None,
        stderr_head="",
        turn_number=None,
    )


def _deliver_via_cli(
    ctx: DeferredRecycleContext,
    *,
    subject: str,
    body: str,
) -> dict[str, Any]:
    latest, fetch_code, _fo, fetch_err = _latest_turn_number(ctx)
    if latest is None:
        return _report_record(
            outcome="failed",
            attempts=0,
            exit_code=fetch_code,
            cause="fetch_latest_failed",
            stderr_head=fetch_err,
            turn_number=None,
        )

    after_turn = latest
    post_count = 0
    last_exit = fetch_code
    last_err = fetch_err

    while post_count < _MAX_REPLY_POSTS:
        post_count += 1
        sent_after = after_turn
        timed_out = False
        out = ""
        try:
            last_exit, out, last_err = _run_agent_bus_cli(
                ctx,
                [
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
                    str(after_turn),
                ],
            )
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            last_exit = -1
            out = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            last_err = "TimeoutExpired"

        if last_exit == 0:
            turn_num = _parse_reply_turn(out)
            _log_reply_success(turn_num)
            return _report_record(
                outcome="delivered",
                attempts=post_count,
                exit_code=0,
                cause=None,
                stderr_head=last_err,
                turn_number=turn_num,
            )

        _log_reply_failure(
            exit_code=last_exit,
            attempt=post_count,
            thread=str(ctx.report_thread),
            stderr=last_err,
            stdout=out,
        )

        if "409 Conflict" in last_err:
            refreshed, ref_code, _rfo, ref_err = _latest_turn_number(ctx)
            if refreshed is None:
                return _report_record(
                    outcome="failed",
                    attempts=post_count,
                    exit_code=ref_code,
                    cause="409_refresh_fetch_failed",
                    stderr_head=ref_err,
                    turn_number=None,
                )
            if refreshed > sent_after:
                after_turn = refreshed
                continue
            return _report_record(
                outcome="failed",
                attempts=post_count,
                exit_code=last_exit,
                cause="409_conflict",
                stderr_head=last_err,
                turn_number=None,
            )

        kind = _classify_reply_stderr(last_err, timed_out=timed_out)
        turns, rb_code, _rbo, rb_err = _fetch_turns(ctx, last=20)
        if turns is None:
            return _report_record(
                outcome="failed",
                attempts=post_count,
                exit_code=last_exit,
                cause="readback_fetch_failed",
                stderr_head=rb_err or last_err,
                turn_number=None,
            )

        matched = _readback_match(turns, high_water=sent_after, subject=subject)
        if matched is not None:
            _log_reply_success(matched)
            return _report_record(
                outcome="delivered",
                attempts=post_count,
                exit_code=last_exit,
                cause=None,
                stderr_head=last_err,
                turn_number=matched,
            )

        if kind == "timeout":
            cause = "timeout delivery_unverified"
            return _report_record(
                outcome="failed",
                attempts=post_count,
                exit_code=last_exit,
                cause=cause,
                stderr_head=last_err,
                turn_number=None,
            )

        if kind in ("request_not_sent", "server_error"):
            oldest = min(
                (int(t["turn_number"]) for t in turns if isinstance(t.get("turn_number"), int)),
                default=None,
            )
            if oldest is not None and oldest <= sent_after and post_count < _MAX_REPLY_POSTS:
                after_turn = max(
                    int(t["turn_number"])
                    for t in turns
                    if isinstance(t.get("turn_number"), int)
                )
                continue
            return _report_record(
                outcome="failed",
                attempts=post_count,
                exit_code=last_exit,
                cause=kind,
                stderr_head=last_err,
                turn_number=None,
            )

        return _report_record(
            outcome="failed",
            attempts=post_count,
            exit_code=last_exit,
            cause="unclassified delivery_unverified",
            stderr_head=last_err,
            turn_number=None,
        )

    return _report_record(
        outcome="failed",
        attempts=post_count,
        exit_code=last_exit,
        cause="post_cap_exhausted",
        stderr_head=last_err,
        turn_number=None,
    )


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
) -> dict[str, Any]:
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
    try:
        if reply_fn is not None:
            return _deliver_via_reply_fn(
                ctx, reply_fn=reply_fn, subject=subject, body=body
            )
        return _deliver_via_cli(ctx, subject=subject, body=body)
    except Exception as exc:
        return _report_record(
            outcome="failed",
            attempts=0,
            exit_code=None,
            cause=f"{type(exc).__name__}: {exc}",
            stderr_head="",
            turn_number=None,
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
    report = post_maestro_reply(
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
    crashed.report = report
    persist_result_projection(crashed, ctx.result_path)
