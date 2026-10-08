"""Seat-fireable prelude: wait convergent guards, GIW paired stop, operator HOME.

External to the manage PID — uses manage.sock and host paths only.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.model_manager.ui.controller.restart_intent_states import (
    _NEEDS_RECONCILE,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)

from .checks import (
    RefuseFinding,
    default_intent_db,
    observe_manage_inflight,
    observe_nonterminal_intents,
    resolve_gateway_dir,
)
from .pane import ppid_of

ManageCall = Callable[..., dict[str, Any]]
_GIW = "git_integration_worker"
_CALLER_DISPATCH_ENV = "CURSOR_SDK_DISPATCH_ID"
_DEFAULT_INTENT_WAIT_S = 600.0
_DEFAULT_INFLIGHT_WAIT_S = 120.0
_POLL_S = 2.0

PANE_RULING = (
    "cosmetic_default_target: re-exec must send quit/start to the tmux pane "
    "whose #{pane_pid} ancestors include manage; DEFAULT_TMUX_TARGET 0:0 is "
    "only a convenience default. Falsifier: send-keys to a pane that does not "
    "host manage leaves whoami pid unchanged and quit_sock_still_up fires."
)


def seat_operator_home() -> Path:
    """Operator passwd home when the process runs under dispatch HOME."""
    from services.git_integration_worker.cursor_home import (
        is_dispatch_home_path,
        operator_real_home,
    )

    home = Path.home()
    if is_dispatch_home_path(home):
        return operator_real_home()
    return home


def seat_python_bin(explicit: str | None, *, default: str) -> str:
    if explicit and explicit != default:
        return explicit
    return str(seat_operator_home() / ".venvs" / "universal" / "bin" / "python")


def _giw_pid(*, manage_pid: int | None) -> int | None:
    pid_path = resolve_gateway_dir(manage_pid=manage_pid) / "git-integration-worker.pid"
    try:
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None


def giw_is_manage_child(*, manage_pid: int) -> bool:
    giw = _giw_pid(manage_pid=manage_pid)
    if giw is None:
        return False
    parent = ppid_of(giw)
    return parent == manage_pid


def giw_has_claimed_occupants(busy: dict[str, Any]) -> bool:
    """True when busy_status shows a GIW write lease or positive active_count."""
    services = busy.get("services")
    if not isinstance(services, dict):
        return False
    entry = services.get(_GIW)
    if not isinstance(entry, dict):
        return False
    active_work = entry.get("active_work")
    if not isinstance(active_work, dict) or not active_work:
        return False
    for key in ("active_count", "total", "running_count", "running"):
        val = active_work.get(key)
        if isinstance(val, int) and val > 0:
            return True
    lease = active_work.get("write_lease")
    if isinstance(lease, dict) and lease.get("holder_dispatch_id"):
        return True
    gate = active_work.get("cursor_sdk_gate")
    if isinstance(gate, dict):
        busy_st = gate.get("busy_status")
        if isinstance(busy_st, dict):
            holder = busy_st.get("active_holder")
            if isinstance(holder, dict) and holder.get("dispatch_id"):
                return True
    return False


def caller_dispatch_id() -> str | None:
    """cursor-sdk dispatch id of this process, when it runs inside one."""
    value = os.environ.get(_CALLER_DISPATCH_ENV, "").strip()
    return value or None


def observe_giw_occupants(
    busy: dict[str, Any],
    *,
    caller_dispatch: str | None,
) -> RefuseFinding | None:
    """Refuse when GIW has claimed occupants, counting the calling dispatch.

    Independent of ``manage_inflight_others`` and of whether GIW is a manage
    child: a seat running inside a cursor-sdk dispatch is itself a GIW
    occupant even when busy_status has not caught up with it. Read-only, so
    dry-run and execute run the same check.
    """
    offenders: list[dict[str, Any]] = []
    if caller_dispatch:
        offenders.append(
            {"occupant": "caller_dispatch", "dispatch_id": caller_dispatch}
        )
    if giw_has_claimed_occupants(busy):
        entry = busy["services"][_GIW]
        offenders.append(
            {"occupant": "busy_status", "active_work": entry.get("active_work")}
        )
    if not offenders:
        return None
    return RefuseFinding(reason="giw_claimed_occupants", offenders=offenders)


def _service_stopped(manage_call: ManageCall, service: str) -> bool:
    """True only when manage reports the service process is genuinely stopped.

    Instrument misses (unhealthy, unknown, error) must not nudge restart
    convergence — a timeout-derived unhealthy is not a stopped process.
    """
    health = manage_call("health", {"service": service})
    status = str(health.get("status") or "").lower()
    return status == "stopped"


def _nudge_dead_intent_converge(
    manage_call: ManageCall,
    offenders: list[dict[str, Any]],
) -> None:
    """Ask manage to reconcile intents whose target process is already gone."""
    seen: set[str] = set()
    for row in offenders:
        service = str(row.get("service") or "")
        if not service or service in seen:
            continue
        seen.add(service)
        if _service_stopped(manage_call, service):
            manage_call("sync_restart", {"service": service})


@dataclass(slots=True)
class IntentWaitResult:
    cleared: bool
    waited_s: float
    last_offenders: list[dict[str, Any]] = field(default_factory=list)


def wait_nonterminal_intents_clear(
    *,
    store: RestartIntentStore,
    manage_call: ManageCall,
    timeout_s: float = _DEFAULT_INTENT_WAIT_S,
    poll_s: float = _POLL_S,
) -> IntentWaitResult:
    """Poll until no intent remains in ``_NEEDS_RECONCILE``, nudging dead targets."""
    deadline = time.monotonic() + timeout_s
    last_offenders: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        finding = observe_nonterminal_intents(store)
        if finding is None:
            return IntentWaitResult(
                cleared=True, waited_s=timeout_s - (deadline - time.monotonic())
            )
        last_offenders = list(finding.offenders)
        _nudge_dead_intent_converge(manage_call, last_offenders)
        time.sleep(poll_s)
    return IntentWaitResult(
        cleared=False,
        waited_s=timeout_s,
        last_offenders=last_offenders,
    )


@dataclass(slots=True)
class InflightWaitResult:
    cleared: bool
    waited_s: float
    giw_paired_required: bool = False
    giw_paired_refused_occupants: bool = False


def resolve_manage_inflight_for_seat(
    busy: dict[str, Any],
    *,
    manage_pid: int,
    manage_call: ManageCall,
    timeout_s: float = _DEFAULT_INFLIGHT_WAIT_S,
    poll_s: float = _POLL_S,
) -> InflightWaitResult:
    """Wait for manage.sock quiescence or admit GIW-child paired stop/start.

    ``observe_manage_inflight`` refuses when ``manage_inflight_others > 0`` or
    named ``activities`` are live (long-running TUI work on the manage host).
    When the sole practical blocker is GIW parented by manage, return
    ``giw_paired_required`` instead of refusing — execute path stops GIW first.
    """
    deadline = time.monotonic() + timeout_s
    giw_child = giw_is_manage_child(manage_pid=manage_pid)
    while time.monotonic() < deadline:
        finding, _raw, _others, activities = observe_manage_inflight(busy)
        if finding is None:
            return InflightWaitResult(
                cleared=True,
                waited_s=max(0.0, timeout_s - (deadline - time.monotonic())),
            )
        if giw_child and not activities:
            if giw_has_claimed_occupants(busy):
                return InflightWaitResult(
                    cleared=False,
                    waited_s=0.0,
                    giw_paired_required=False,
                    giw_paired_refused_occupants=True,
                )
            return InflightWaitResult(
                cleared=True,
                waited_s=0.0,
                giw_paired_required=True,
            )
        busy = manage_call("busy_status", {})
        time.sleep(poll_s)
    return InflightWaitResult(cleared=False, waited_s=timeout_s)


def run_giw_paired_stop(
    manage_call: ManageCall,
    *,
    manage_pid: int,
) -> dict[str, Any]:
    """Supervised GIW stop while manage is still up (438f3d53 drain semantics)."""
    if giw_has_claimed_occupants(manage_call("busy_status", {})):
        return {
            "status": "refused",
            "reason": "giw_claimed_occupants",
        }
    if not giw_is_manage_child(manage_pid=manage_pid):
        return {"status": "skipped", "reason": "giw_not_manage_child"}
    return manage_call(
        "stop",
        {"service": _GIW, "park_live": True},
        timeout=1830.0,
    )


def run_giw_paired_start(manage_call: ManageCall) -> dict[str, Any]:
    """Start GIW after manage re-exec landed."""
    return manage_call("start", {"service": _GIW}, timeout=300.0)


def intent_store_for_manage(
    *,
    db_path: Path | None,
    manage_pid: int | None,
) -> RestartIntentStore:
    return RestartIntentStore(
        db_path=db_path or default_intent_db(manage_pid=manage_pid)
    )


def is_nonterminal_status(status: str) -> bool:
    return status in _NEEDS_RECONCILE


__all__ = [
    "PANE_RULING",
    "IntentWaitResult",
    "InflightWaitResult",
    "caller_dispatch_id",
    "giw_has_claimed_occupants",
    "giw_is_manage_child",
    "intent_store_for_manage",
    "is_nonterminal_status",
    "observe_giw_occupants",
    "resolve_manage_inflight_for_seat",
    "run_giw_paired_start",
    "run_giw_paired_stop",
    "seat_operator_home",
    "seat_python_bin",
    "wait_nonterminal_intents_clear",
]
