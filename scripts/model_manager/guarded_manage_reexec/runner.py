"""Orchestrate guarded manage quit/start with structured proof verdicts.

Sequence: refuse checks → arm successor (tmux pane + armed record) → pre-quit
recheck → quit incumbent → dual whoami proof. Dry-run stops before spawn.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from transport_utils import MANAGE_SOCKET
from universal_event_bus import Event, event_factory

from libs.manage_handover import (
    prove_armed_record,
    read_armed_record,
    remove_armed_record,
)

from .checks import (
    RefuseFinding,
    collect_refuse_report,
    observe_drain_clear,
    observe_manage_inflight,
    observe_nonterminal_intents,
)
from .client import call_manage
from .pane import (
    TreeContainsFn,
    find_tmux_target_hosting_manage,
    kill_successor_pane,
    observe_tmux_pane_hosts_manage,
    spawn_successor_pane,
)
from .result import (
    DEFAULT_BOOT_TIMEOUT_S,
    DEFAULT_QUIT_TIMEOUT_S,
    GuardedReexecResult,
    prove_pickup,
)
from .seat_prelude import (
    PANE_RULING,
    caller_dispatch_id,
    intent_store_for_manage,
    observe_giw_occupants,
    resolve_manage_inflight_for_seat,
    run_giw_paired_start,
    run_giw_paired_stop,
    seat_operator_home,
    seat_python_bin,
    wait_nonterminal_intents_clear,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TMUX_TARGET = "0:0"
DEFAULT_PYTHON = str(seat_operator_home() / ".venvs" / "universal" / "bin" / "python")

ManageCall = Callable[..., dict[str, Any]]
RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]
KillPid = Callable[[int], None]

__all__ = [
    "DEFAULT_BOOT_TIMEOUT_S",
    "DEFAULT_QUIT_TIMEOUT_S",
    "DEFAULT_TMUX_TARGET",
    "GuardedReexecResult",
    "prove_pickup",
    "run_guarded_reexec",
]


@event_factory
def ManageReexecArmed(pane_id: str, record_path: str) -> Event:  # noqa: N802
    return Event(
        signal="manage.reexec.armed",
        payload={"pane_id": pane_id, "record_path": record_path},
    )


@event_factory
def ManageReexecRefused(reason: str) -> Event:  # noqa: N802
    return Event(signal="manage.reexec.refused", payload={"reason": reason})


@event_factory
def ManageReexecQuitCommitted(tmux_target: str) -> Event:  # noqa: N802
    return Event(
        signal="manage.reexec.quit_committed",
        payload={"tmux_target": tmux_target},
    )


@event_factory
def ManageReexecProof(status: str, reason: str) -> Event:  # noqa: N802
    return Event(
        signal="manage.reexec.proof",
        payload={"status": status, "reason": reason},
    )


def _publish_reexec(event: Event) -> None:
    """Best-effort UDS publish; silent on failure (sync runner has no EventBus)."""
    from scripts.model_manager.observation_event import _emit_sync

    payload = dict(event.payload) if isinstance(event.payload, dict) else {}
    _emit_sync(event.signal, payload, role=event.role, scope=event.scope)


def _emit_refused(reason: str) -> None:
    _publish_reexec(ManageReexecRefused(reason=reason))


def _default_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


def _default_kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _tmux_send(target: str, keys: str, *, run_cmd: RunCmd) -> None:
    run_cmd(["tmux", "send-keys", "-t", target, keys, "Enter"])


def _wait_sock(
    sock_path: str,
    *,
    manage_call: ManageCall,
    timeout_s: float,
    want_up: bool,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = manage_call("whoami", {}, sock_path=sock_path, timeout=2.0)
        up = result.get("status") != "error" and "pid" in result
        if up == want_up:
            return True
        time.sleep(0.5)
    return False


def _teardown_successor(
    *,
    pane_id: str | None,
    record_path: Path,
    record_pid: int | None,
    run_cmd: RunCmd,
    kill_pid_fn: KillPid,
) -> None:
    kill_successor_pane(pane_id, run_cmd=run_cmd)
    if record_pid is not None:
        kill_pid_fn(record_pid)
    remove_armed_record(record_path)


def _poll_armed_record(
    record_path: Path,
    *,
    target_ref: str,
    whoami_before: dict[str, Any],
    boot_timeout_s: float,
) -> tuple[bool, str, dict | None]:
    deadline = time.monotonic() + boot_timeout_s
    while time.monotonic() < deadline:
        record = read_armed_record(record_path)
        if record is not None:
            ok, detail = prove_armed_record(
                record, target_ref=target_ref, whoami_before=whoami_before
            )
            if ok:
                return True, detail, record
            return False, detail, record
        time.sleep(0.25)
    return False, "successor_not_armed", None


def _dry_run_attach_drain(report: Any, hold: dict[str, Any]) -> None:
    """Fold hold_status drain observation into a dry-run check report."""
    report.hold_status = hold
    if hold.get("status") == "error":
        report.findings.append(
            RefuseFinding(reason="hold_status_unobservable", offenders=[hold])
        )
        report.refused = True
        return
    report.held = bool(hold.get("held"))
    report.pause_drain_clear = bool(hold.get("pause_drain_clear"))


def _attach_finding(report: Any, finding: RefuseFinding | None) -> None:
    if finding is None:
        return
    report.findings.append(finding)
    report.refused = True


def _refused_result(
    *,
    dry_run: bool,
    reason: str,
    whoami_before: dict[str, Any],
    target_ref: str,
    report_dict: dict[str, Any],
    boot_timeout_s: float,
    quit_timeout_s: float,
) -> GuardedReexecResult:
    return GuardedReexecResult(
        status="dry-run" if dry_run else "refused",
        reason=reason,
        dry_run=dry_run,
        checks=report_dict,
        whoami_before=whoami_before,
        target_ref=target_ref,
        executed=False,
        boot_timeout_s=boot_timeout_s,
        quit_timeout_s=quit_timeout_s,
    )


def run_guarded_reexec(
    *,
    target_ref: str,
    dry_run: bool = True,
    sock_path: str = MANAGE_SOCKET,
    tmux_target: str = DEFAULT_TMUX_TARGET,
    repo_root: Path = REPO_ROOT,
    python_bin: str = DEFAULT_PYTHON,
    manage_call: ManageCall | None = None,
    run_cmd: RunCmd | None = None,
    kill_pid_fn: KillPid | None = None,
    intent_db: Path | None = None,
    armed_record_path: Path | None = None,
    pause_reason: str = "guarded_manage_reexec",
    quit_timeout_s: float = DEFAULT_QUIT_TIMEOUT_S,
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S,
    tree_contains_fn: TreeContainsFn | None = None,
    intent_wait_s: float = 600.0,
    inflight_wait_s: float = 120.0,
    max_start_attempts: int | None = None,
) -> GuardedReexecResult:
    del max_start_attempts  # CLI compat; arm-then-quit uses a single successor spawn.
    """Run refuse/arm/quit/proof path; dry_run never spawns a successor."""
    manage_call = manage_call or call_manage
    run_cmd = run_cmd or _default_run
    kill_pid_fn = kill_pid_fn or _default_kill_pid
    python_bin = seat_python_bin(python_bin, default=DEFAULT_PYTHON)
    seat_home = seat_operator_home()
    record_path = armed_record_path or (
        Path(MANAGE_SOCKET).parent / "manage.armed.json"
    )

    def _busy() -> dict[str, Any]:
        return manage_call("busy_status", {}, sock_path=sock_path)

    def _hold() -> dict[str, Any]:
        return manage_call("charter_hold_status", {}, sock_path=sock_path)

    whoami_before = manage_call("whoami", {}, sock_path=sock_path)
    if whoami_before.get("status") == "error":
        _emit_refused("whoami_unobservable_before")
        return GuardedReexecResult(
            status="refused",
            reason="whoami_unobservable_before",
            dry_run=dry_run,
            whoami_before=whoami_before,
            target_ref=target_ref,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
            checks={
                "findings": [
                    {
                        "reason": "whoami_unobservable_before",
                        "offenders": [whoami_before],
                    }
                ]
            },
        )

    manage_pid = whoami_before.get("pid")
    manage_pid_i = int(manage_pid) if isinstance(manage_pid, int) else None
    store = intent_store_for_manage(db_path=intent_db, manage_pid=manage_pid_i)
    report = collect_refuse_report(
        busy_status_fn=_busy,
        hold_status_fn=_hold,
        store=store,
        manage_pid=manage_pid_i,
        require_drain_clear=False,
    )
    if dry_run:
        _dry_run_attach_drain(report, _hold())

    # Same occupant guard on dry-run and execute; the caller counts as one.
    occupant_finding = observe_giw_occupants(
        report.busy_status or {}, caller_dispatch=caller_dispatch_id()
    )
    _attach_finding(report, occupant_finding)

    tmux_effective = tmux_target
    pane_meta: dict[str, Any] = {"pane_ruling": PANE_RULING}
    if manage_pid_i is None:
        _attach_finding(
            report,
            RefuseFinding(
                reason="manage_pid_unobservable",
                offenders=[{"whoami": whoami_before}],
            ),
        )
    else:
        pane_finding = observe_tmux_pane_hosts_manage(
            tmux_target=tmux_effective,
            manage_pid=manage_pid_i,
            run_cmd=run_cmd,
            tree_contains_fn=tree_contains_fn,
        )
        if pane_finding is not None and pane_finding.reason == "tmux_pane_pid_mismatch":
            resolved, scan = find_tmux_target_hosting_manage(
                manage_pid_i,
                run_cmd=run_cmd,
                tree_contains_fn=tree_contains_fn,
            )
            if resolved:
                tmux_effective = resolved
                pane_meta |= {
                    "tmux_target_requested": tmux_target,
                    "tmux_target_effective": tmux_effective,
                    "tmux_scan": scan,
                }
                pane_finding = observe_tmux_pane_hosts_manage(
                    tmux_target=tmux_effective,
                    manage_pid=manage_pid_i,
                    run_cmd=run_cmd,
                    tree_contains_fn=tree_contains_fn,
                )
        _attach_finding(report, pane_finding)

    prelude: dict[str, Any] = {
        "seat_operator_home": str(seat_home),
        **pane_meta,
    }
    giw_paired_required = False

    # Occupied => refuse now; the waits below can nudge sync_restart.
    if not dry_run and manage_pid_i is not None and occupant_finding is None:
        intent_wait = wait_nonterminal_intents_clear(
            store=store,
            manage_call=manage_call,
            timeout_s=intent_wait_s,
        )
        prelude["intent_wait"] = {
            "cleared": intent_wait.cleared,
            "waited_s": intent_wait.waited_s,
            "last_offenders": intent_wait.last_offenders,
        }
        if not intent_wait.cleared:
            report.findings = [
                f for f in report.findings if f.reason != "nonterminal_restart_intent"
            ]
            report.findings.append(
                RefuseFinding(
                    reason="nonterminal_restart_intent",
                    offenders=intent_wait.last_offenders,
                )
            )
            report.refused = True
        else:
            report.findings = [
                f for f in report.findings if f.reason != "nonterminal_restart_intent"
            ]

        inflight = resolve_manage_inflight_for_seat(
            _busy(),
            manage_pid=manage_pid_i,
            manage_call=manage_call,
            timeout_s=inflight_wait_s,
        )
        prelude["inflight_wait"] = {
            "cleared": inflight.cleared,
            "waited_s": inflight.waited_s,
            "giw_paired_required": inflight.giw_paired_required,
            "giw_paired_refused_occupants": inflight.giw_paired_refused_occupants,
        }
        if inflight.giw_paired_refused_occupants:
            report.findings.append(
                RefuseFinding(
                    reason="giw_claimed_occupants",
                    offenders=[{"detail": "never kill GIW with claimed occupants"}],
                )
            )
            report.refused = True
        elif inflight.giw_paired_required:
            giw_paired_required = True
            report.findings = [
                f
                for f in report.findings
                if f.reason != "manage_inflight_or_activities"
            ]
            report.refused = bool(report.findings)
        elif not inflight.cleared:
            report.refused = True
        else:
            report.findings = [
                f
                for f in report.findings
                if f.reason != "manage_inflight_or_activities"
            ]
            report.refused = bool(report.findings)
    elif dry_run:
        intent_finding = observe_nonterminal_intents(store)
        if intent_finding is not None:
            prelude["intent_wait"] = {
                "cleared": False,
                "dry_run": True,
                "offenders": intent_finding.offenders,
            }

    report_dict = report.as_dict()
    report_dict["seat_prelude"] = prelude

    if report.refused:
        reason = ";".join(f.reason for f in report.findings) or "refused"
        if not dry_run:
            _emit_refused(reason)
        return _refused_result(
            dry_run=dry_run,
            reason=reason,
            whoami_before=whoami_before,
            target_ref=target_ref,
            report_dict=report_dict,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    if dry_run:
        return GuardedReexecResult(
            status="dry-run",
            reason="checks_passed_stopped_before_quit",
            dry_run=True,
            checks=report_dict,
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    giw_paired: dict[str, Any] | None = None
    if giw_paired_required:
        stop_payload = run_giw_paired_stop(manage_call, manage_pid=manage_pid_i or 0)
        giw_paired = {"stop": stop_payload}
        if stop_payload.get("status") == "refused":
            _emit_refused("giw_claimed_occupants")
            return GuardedReexecResult(
                status="refused",
                reason="giw_claimed_occupants",
                dry_run=False,
                checks={**report_dict, "giw_paired": giw_paired},
                whoami_before=whoami_before,
                target_ref=target_ref,
                executed=False,
                boot_timeout_s=boot_timeout_s,
                quit_timeout_s=quit_timeout_s,
            )

    pause = manage_call(
        "charter_pause",
        {"reason": pause_reason, "set_by": "guarded_manage_reexec"},
        sock_path=sock_path,
        timeout=1830.0,
    )
    hold_after = manage_call("charter_hold_status", {}, sock_path=sock_path)
    drain = observe_drain_clear(
        hold_after if hold_after.get("status") != "error" else {}
    )
    if drain is not None:
        _emit_refused("drain_not_clear_after_pause")
        return GuardedReexecResult(
            status="refused",
            reason="drain_not_clear_after_pause",
            dry_run=False,
            checks={
                "pause": pause,
                "hold_after": hold_after,
                "findings": [{"reason": drain.reason, "offenders": drain.offenders}],
            },
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    successor_pane_id: str | None = None
    record_pid: int | None = None
    t_arm_start = time.monotonic()

    def _refuse_after_arm(reason: str) -> GuardedReexecResult:
        _emit_refused(reason)
        _teardown_successor(
            pane_id=successor_pane_id,
            record_path=record_path,
            record_pid=record_pid,
            run_cmd=run_cmd,
            kill_pid_fn=kill_pid_fn,
        )
        manage_call("charter_resume", {}, sock_path=sock_path)
        return GuardedReexecResult(
            status="refused",
            reason=reason,
            dry_run=False,
            checks={
                **report_dict,
                "successor_pane_id": successor_pane_id,
                "armed_record_path": str(record_path),
                "elapsed_arm_s": round(time.monotonic() - t_arm_start, 3),
            },
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    remove_armed_record(record_path)
    successor_pane_id = spawn_successor_pane(
        tmux_target=tmux_effective,
        repo_root=repo_root,
        python_bin=python_bin,
        record_path=record_path,
        run_cmd=run_cmd,
    )
    if not successor_pane_id:
        return _refuse_after_arm("successor_spawn_failed")

    armed_ok, arm_detail, record = _poll_armed_record(
        record_path,
        target_ref=target_ref,
        whoami_before=whoami_before,
        boot_timeout_s=boot_timeout_s,
    )
    if not armed_ok:
        if record is not None:
            record_pid_val = record.get("pid")
            record_pid = (
                int(record_pid_val) if isinstance(record_pid_val, int) else None
            )
        reason = arm_detail if arm_detail == "successor_not_armed" else arm_detail
        return _refuse_after_arm(reason)

    assert record is not None
    record_pid_val = record.get("pid")
    record_pid = int(record_pid_val) if isinstance(record_pid_val, int) else None

    _publish_reexec(
        ManageReexecArmed(
            pane_id=successor_pane_id or "",
            record_path=str(record_path),
        )
    )

    intent_finding = observe_nonterminal_intents(store)
    if intent_finding is not None:
        return _refuse_after_arm("nonterminal_restart_intent")

    busy_recheck = _busy()
    if busy_recheck.get("status") == "error":
        return _refuse_after_arm("busy_status_unobservable")
    inflight_recheck, _, _, _ = observe_manage_inflight(busy_recheck)
    if inflight_recheck is not None:
        return _refuse_after_arm(inflight_recheck.reason)

    hold_recheck = _hold()
    if hold_recheck.get("status") == "error":
        return _refuse_after_arm("hold_status_unobservable")
    drain_before_quit = observe_drain_clear(hold_recheck)
    if drain_before_quit is not None:
        return _refuse_after_arm("drain_not_clear_before_quit")

    _tmux_send(tmux_effective, "q", run_cmd=run_cmd)
    _publish_reexec(ManageReexecQuitCommitted(tmux_target=tmux_effective))

    if not _wait_sock(
        sock_path, manage_call=manage_call, timeout_s=quit_timeout_s, want_up=False
    ):
        _teardown_successor(
            pane_id=successor_pane_id,
            record_path=record_path,
            record_pid=record_pid,
            run_cmd=run_cmd,
            kill_pid_fn=kill_pid_fn,
        )
        _publish_reexec(ManageReexecProof(status="quit", reason="quit_sock_still_up"))
        return GuardedReexecResult(
            status="quit",
            reason="quit_sock_still_up",
            dry_run=False,
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=True,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
            checks={
                "successor_pane_id": successor_pane_id,
                "record_pid": record_pid,
            },
        )

    if not _wait_sock(
        sock_path,
        manage_call=manage_call,
        timeout_s=boot_timeout_s,
        want_up=True,
    ):
        _publish_reexec(
            ManageReexecProof(
                status="proof-failed", reason="successor_bind_not_observed"
            )
        )
        return GuardedReexecResult(
            status="proof-failed",
            reason="successor_bind_not_observed",
            dry_run=False,
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=True,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
            checks={
                "successor_pane_id": successor_pane_id,
                "armed_record_path": str(record_path),
                "record_pid": record_pid,
            },
        )

    whoami_after = manage_call("whoami", {}, sock_path=sock_path)
    if giw_paired_required:
        giw_paired = giw_paired or {}
        giw_paired["start"] = run_giw_paired_start(manage_call)
    manage_call("charter_resume", {}, sock_path=sock_path)
    version_ok, start_ok, proof_reason = prove_pickup(
        before=whoami_before, after=whoami_after, target_ref=target_ref
    )
    status = "proof-satisfied" if (version_ok and start_ok) else "proof-failed"
    _publish_reexec(ManageReexecProof(status=status, reason=proof_reason))
    return GuardedReexecResult(
        status=status,
        reason=proof_reason,
        dry_run=False,
        whoami_before=whoami_before,
        whoami_after=whoami_after,
        target_ref=target_ref,
        code_version_ok=version_ok,
        process_start_later_ok=start_ok,
        executed=True,
        boot_timeout_s=boot_timeout_s,
        quit_timeout_s=quit_timeout_s,
        checks={
            "pause": pause,
            "hold_after": hold_after,
            "seat_prelude": prelude,
            "giw_paired": giw_paired,
            "tmux_target_effective": tmux_effective,
            "successor_pane_id": successor_pane_id,
        },
    )
