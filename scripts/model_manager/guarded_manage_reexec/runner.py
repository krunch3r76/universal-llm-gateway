"""Orchestrate guarded manage quit/start with structured proof verdicts.

Sequence: refuse checks → pane-identity (live manage pid) → charter_pause →
drain-clear → quit → re-exec ``python -m scripts.model_manager.ui`` →
charter_resume → dual whoami proof (code_version match AND process_start_time
later than pre-quit). Dry-run stops before quit and before any pause mutation.

Recovery after quit-ok / start-fail: **manual, human at the terminal** — manage
is not in ``VALID_SERVICES``, has no systemd/supervisord unit, and this package
does not retry. Re-drive ``tmux`` ``0:0`` (or the verified target) with
``./manage`` per ``services_ws`` safe quit/start recipe.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from transport_utils import MANAGE_SOCKET

from services.git_integration_worker.dispatch_home_host_guard import (
    DispatchHomeHostRefusal,
    refuse_host_target_if_dispatch_home,
)

from .checks import (
    RefuseFinding,
    collect_refuse_report,
    observe_drain_clear,
)
from .client import call_manage
from .pane import TreeContainsFn, observe_tmux_pane_hosts_manage
from .result import (
    DEFAULT_BOOT_TIMEOUT_S,
    DEFAULT_QUIT_TIMEOUT_S,
    RECOVERY_PATH,
    GuardedReexecResult,
    prove_pickup,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TMUX_TARGET = "0:0"
DEFAULT_PYTHON = str(Path.home() / ".venvs" / "universal" / "bin" / "python")

ManageCall = Callable[..., dict[str, Any]]
RunCmd = Callable[[list[str]], subprocess.CompletedProcess[str]]

__all__ = [
    "DEFAULT_BOOT_TIMEOUT_S",
    "DEFAULT_QUIT_TIMEOUT_S",
    "DEFAULT_TMUX_TARGET",
    "GuardedReexecResult",
    "RECOVERY_PATH",
    "prove_pickup",
    "run_guarded_reexec",
]


def _default_run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=False, text=True, capture_output=True)


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
    drain = observe_drain_clear(hold)
    if drain is not None:
        report.findings.append(drain)
        report.refused = True


def _attach_pane_finding(report: Any, finding: RefuseFinding | None) -> None:
    if finding is None:
        return
    report.findings.append(finding)
    report.refused = True


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
    intent_db: Path | None = None,
    pause_reason: str = "guarded_manage_reexec",
    quit_timeout_s: float = DEFAULT_QUIT_TIMEOUT_S,
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S,
    tree_contains_fn: TreeContainsFn | None = None,
) -> GuardedReexecResult:
    """Run refuse/require/proof path; dry_run never quits or pauses charter hold."""
    manage_call = manage_call or call_manage
    run_cmd = run_cmd or _default_run

    try:
        refuse_host_target_if_dispatch_home(tool="guarded_manage_reexec")
    except DispatchHomeHostRefusal as exc:
        return GuardedReexecResult(
            status="dry-run" if dry_run else "refused",
            reason="dispatch_home_host_refusal",
            dry_run=dry_run,
            checks={
                "findings": [
                    {
                        "reason": "dispatch_home_host_refusal",
                        "offenders": [{"message": str(exc)}],
                    }
                ],
                "refused": True,
            },
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    def _busy() -> dict[str, Any]:
        return manage_call("busy_status", {}, sock_path=sock_path)

    def _hold() -> dict[str, Any]:
        return manage_call("charter_hold_status", {}, sock_path=sock_path)

    whoami_before = manage_call("whoami", {}, sock_path=sock_path)
    if whoami_before.get("status") == "error":
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

    # Dry-run skips mutate-path drain require; observes drain read-only below.
    manage_pid = whoami_before.get("pid")
    manage_pid_i = int(manage_pid) if isinstance(manage_pid, int) else None
    report = collect_refuse_report(
        busy_status_fn=_busy,
        hold_status_fn=_hold,
        db_path=intent_db,
        manage_pid=manage_pid_i,
        require_drain_clear=not dry_run,
    )
    if dry_run:
        _dry_run_attach_drain(report, _hold())

    if manage_pid_i is None:
        _attach_pane_finding(
            report,
            RefuseFinding(
                reason="manage_pid_unobservable",
                offenders=[{"whoami": whoami_before}],
            ),
        )
    else:
        _attach_pane_finding(
            report,
            observe_tmux_pane_hosts_manage(
                tmux_target=tmux_target,
                manage_pid=manage_pid_i,
                run_cmd=run_cmd,
                tree_contains_fn=tree_contains_fn,
            ),
        )

    if report.refused:
        return GuardedReexecResult(
            status="dry-run" if dry_run else "refused",
            reason=";".join(f.reason for f in report.findings) or "refused",
            dry_run=dry_run,
            checks=report.as_dict(),
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    if dry_run:
        return GuardedReexecResult(
            status="dry-run",
            reason="checks_passed_stopped_before_quit",
            dry_run=True,
            checks=report.as_dict(),
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    # ── mutate path (operator-authorized only; this dispatch does not run it)
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
        return GuardedReexecResult(
            status="refused",
            reason="drain_not_clear_after_pause",
            dry_run=False,
            checks={
                "pause": pause,
                "hold_after": hold_after,
                "findings": [
                    {"reason": drain.reason, "offenders": drain.offenders}
                ],
            },
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=False,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    _tmux_send(tmux_target, "q", run_cmd=run_cmd)
    if not _wait_sock(
        sock_path, manage_call=manage_call, timeout_s=quit_timeout_s, want_up=False
    ):
        return GuardedReexecResult(
            status="quit",
            reason="quit_sock_still_up",
            dry_run=False,
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=True,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
        )

    start_cmd = f"cd {repo_root} && {python_bin} -m scripts.model_manager.ui"
    _tmux_send(tmux_target, start_cmd, run_cmd=run_cmd)
    if not _wait_sock(
        sock_path, manage_call=manage_call, timeout_s=boot_timeout_s, want_up=True
    ):
        # Quit succeeded; start/health did not. Distinct from status=refused
        # (precondition — manage still up, nothing destroyed).
        return GuardedReexecResult(
            status="start-failed",
            reason="reexec_sock_not_up",
            dry_run=False,
            whoami_before=whoami_before,
            target_ref=target_ref,
            executed=True,
            boot_timeout_s=boot_timeout_s,
            quit_timeout_s=quit_timeout_s,
            checks={
                "pause": pause,
                "hold_after": hold_after,
                "recovery_path": RECOVERY_PATH,
                "note": (
                    "manage quit landed; sock never returned within "
                    f"boot_timeout_s={boot_timeout_s}"
                ),
            },
        )

    whoami_after = manage_call("whoami", {}, sock_path=sock_path)
    manage_call("charter_resume", {}, sock_path=sock_path)
    version_ok, start_ok, proof_reason = prove_pickup(
        before=whoami_before, after=whoami_after, target_ref=target_ref
    )
    status = "proof-satisfied" if (version_ok and start_ok) else "proof-failed"
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
        checks={"pause": pause, "hold_after": hold_after},
    )
