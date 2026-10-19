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


def _dict_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _lease_and_gate(
    active_work: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    lease = active_work.get("write_lease")
    gate = active_work.get("cursor_sdk_gate")
    lease_d = lease if isinstance(lease, dict) else {}
    gate_d = gate if isinstance(gate, dict) else {}
    busy_st = gate_d.get("busy_status")
    return lease_d, busy_st if isinstance(busy_st, dict) else {}


def _gate_holder_ids(active_work: dict[str, Any]) -> list[str]:
    """``cursor_sdk_gate.holders`` standard then operator, strings only."""
    gate = active_work.get("cursor_sdk_gate")
    if not isinstance(gate, dict):
        return []
    holders = gate.get("holders")
    if not isinstance(holders, dict):
        return []
    ids: list[str] = []
    for lane in ("standard", "operator"):
        lane_ids = holders.get(lane)
        if not isinstance(lane_ids, list):
            continue
        for item in lane_ids:
            if isinstance(item, str) and item:
                ids.append(item)
    return ids


def _write_holder_ids(lease: dict[str, Any]) -> list[str]:
    """``write_lease.write_holders`` when the key is present."""
    raw = lease.get("write_holders")
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            ids.append(item)
            continue
        if not isinstance(item, dict):
            continue
        hid = item.get("holder_dispatch_id") or item.get("dispatch_id")
        if isinstance(hid, str) and hid:
            ids.append(hid)
    return ids


def _caller_attributed(
    active_work: dict[str, Any],
    exclude: str,
    lease: dict[str, Any],
    busy_st: dict[str, Any],
) -> bool:
    """True when the payload positively names ``exclude`` as the occupant.

    Gate holders and ``write_holders`` are part of this set so a holder-only
    caller satisfies the ``active_count == 1`` rule. The live_writers offset
    is this set union holders union write_holders, which equals this set.
    """
    if lease.get("holder_dispatch_id") == exclude:
        return True
    for row in _dict_rows(lease.get("active_holders")):
        if row.get("holder_dispatch_id") == exclude:
            return True
    holder = busy_st.get("active_holder")
    if isinstance(holder, dict) and holder.get("dispatch_id") == exclude:
        return True
    for row in _dict_rows(busy_st.get("active_holders")):
        if row.get("holder_dispatch_id") == exclude:
            return True
    for row in _dict_rows(active_work.get("active_ops")):
        if row.get("kind") == "cursor_sdk" and row.get("op_id") == exclude:
            return True
    if exclude in _gate_holder_ids(active_work):
        return True
    if exclude in _write_holder_ids(lease):
        return True
    return False


def _dedup_by_dispatch_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the first row for each dispatch_id. Count-only rows stay."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for row in rows:
        dispatch_id = row.get("dispatch_id")
        if isinstance(dispatch_id, str) and dispatch_id:
            if dispatch_id in seen:
                continue
            seen.add(dispatch_id)
        kept.append(row)
    return kept


def _giw_active_work(busy: dict[str, Any]) -> dict[str, Any] | None:
    """The GIW active_work dict, or None when the service did not answer."""
    services = busy.get("services")
    if not isinstance(services, dict):
        return None
    entry = services.get(_GIW)
    if not isinstance(entry, dict) or "active_work" not in entry:
        return None
    raw = entry.get("active_work")
    if not isinstance(raw, dict):
        return None
    return raw


def _dry_run_occupancy_readable(active_work: dict[str, Any]) -> bool:
    """True when both holder lists and live_writers are present."""
    gate = active_work.get("cursor_sdk_gate")
    if not isinstance(gate, dict):
        return False
    holders = gate.get("holders")
    if not isinstance(holders, dict):
        return False
    if not isinstance(holders.get("standard"), list):
        return False
    if not isinstance(holders.get("operator"), list):
        return False
    return isinstance(gate.get("live_writers"), int)


def _ledger_reader_rows(raw: list[Any], *, exclude: str) -> list[dict[str, Any]]:
    """Foreign ledger rows. Exclude absent from the rows is itself a refusal."""
    ids: list[str] = []
    for item in raw:
        if isinstance(item, str) and item:
            ids.append(item)
            continue
        if not isinstance(item, dict):
            continue
        hid = item.get("holder_dispatch_id") or item.get("dispatch_id")
        if isinstance(hid, str) and hid:
            ids.append(hid)
    rows: list[dict[str, Any]] = []
    if exclude not in ids:
        rows.append({"source": "ledger_reader", "missing_exclude": exclude})
    seen: set[str] = set()
    for hid in ids:
        if hid == exclude or hid in seen:
            continue
        seen.add(hid)
        rows.append({"source": "ledger_reader", "dispatch_id": hid})
    return rows


def _foreign_giw_rows(
    active_work: dict[str, Any], *, exclude: str
) -> list[dict[str, Any]]:
    """Minimal rows naming each foreign occupant; empty when only the caller is present."""
    rows: list[dict[str, Any]] = []
    lease, busy_st = _lease_and_gate(active_work)
    holder = lease.get("holder_dispatch_id")
    if holder and holder != exclude:
        rows.append({"source": "write_lease.holder_dispatch_id", "dispatch_id": holder})
    for row in _dict_rows(lease.get("active_holders")):
        hid = row.get("holder_dispatch_id")
        if hid and hid != exclude:
            rows.append({"source": "write_lease.active_holders", "dispatch_id": hid})
    gate_holder = busy_st.get("active_holder")
    if isinstance(gate_holder, dict):
        gid = gate_holder.get("dispatch_id")
        if gid and gid != exclude:
            rows.append(
                {
                    "source": "cursor_sdk_gate.busy_status.active_holder",
                    "dispatch_id": gid,
                }
            )
    for row in _dict_rows(busy_st.get("active_holders")):
        hid = row.get("holder_dispatch_id")
        if hid and hid != exclude:
            rows.append(
                {
                    "source": "cursor_sdk_gate.busy_status.active_holders",
                    "dispatch_id": hid,
                }
            )
    for row in _dict_rows(active_work.get("active_ops")):
        if row.get("kind") != "cursor_sdk":
            continue
        op_id = row.get("op_id")
        if op_id and op_id != exclude:
            rows.append({"source": "active_ops", "dispatch_id": op_id})
    lease_queued = _dict_rows(lease.get("queued"))
    for row in lease_queued:
        dispatch_id = row.get("dispatch_id")
        if dispatch_id and dispatch_id != exclude:
            rows.append({"source": "write_lease.queued", "dispatch_id": dispatch_id})
    gate_queued = _dict_rows(busy_st.get("queued_dispatches"))
    for row in gate_queued:
        dispatch_id = row.get("dispatch_id")
        if dispatch_id and dispatch_id != exclude:
            rows.append(
                {
                    "source": "cursor_sdk_gate.busy_status.queued_dispatches",
                    "dispatch_id": dispatch_id,
                }
            )
    if not lease_queued and not gate_queued:
        for depth in (lease.get("queue_depth"), busy_st.get("queue_depth")):
            if isinstance(depth, int) and depth > 0:
                rows.append({"source": "write_lease.queue_depth", "count": depth})
                break
    if not _caller_attributed(active_work, exclude, lease, busy_st):
        for key in ("active_count", "total", "running_count", "running"):
            val = active_work.get(key)
            if isinstance(val, int) and val == 1:
                rows.append({"source": "active_count", "count": val})
                break
    for key in ("active_count", "total", "running_count", "running"):
        val = active_work.get(key)
        if isinstance(val, int) and val > 1:
            rows.append({"source": "active_count", "count": val})
            break
    for hid in _gate_holder_ids(active_work):
        if hid != exclude:
            rows.append({"source": "cursor_sdk_gate.holders", "dispatch_id": hid})
    for hid in _write_holder_ids(lease):
        if hid != exclude:
            rows.append({"source": "write_lease.write_holders", "dispatch_id": hid})
    # Residuals, accepted and not closed here:
    # R-a: live_writers is lanes A and B only. A writer whose lane resolves to
    # unknown is not counted; holders or active_ops still name it when present.
    # R-b: the live_writers count includes admitted/running rows with no
    # live-task filter, so a task that died mid-run can over-count until
    # reconcile. That over-refuses.
    # R-c: an admitted-only Lane-B writer before slot acquire shows only in
    # live_writers. A read-only caller sits in holders and over-offsets by 1,
    # so that one writer can be missed in that window.
    gate = active_work.get("cursor_sdk_gate")
    live = gate.get("live_writers") if isinstance(gate, dict) else None
    # Offset 1 iff exclude ∈ _caller_attributed ∪ holders ∪ write_holders.
    # No read_only inference.
    offset = 1 if _caller_attributed(active_work, exclude, lease, busy_st) else 0
    if isinstance(live, int) and live > offset:
        rows.append({"source": "cursor_sdk_gate.live_writers", "count": live})
    return _dedup_by_dispatch_id(rows)


def giw_foreign_claimed_occupants(
    busy: dict[str, Any], *, exclude_dispatch_id: str
) -> bool:
    """True when busy_status shows GIW work other than ``exclude_dispatch_id``."""
    services = busy.get("services")
    if not isinstance(services, dict):
        return False
    entry = services.get(_GIW)
    if not isinstance(entry, dict):
        return False
    active_work = entry.get("active_work")
    if not isinstance(active_work, dict) or not active_work:
        return False
    return bool(_foreign_giw_rows(active_work, exclude=exclude_dispatch_id))


def _foreign_giw_offenders(
    busy: dict[str, Any], *, exclude_dispatch_id: str
) -> list[dict[str, Any]]:
    """One dry-run offender row: the excluded caller id and foreign rows.

    ``foreign`` dispatch ids are occupants other than ``excluded_dispatch_id``.
    """
    services = busy.get("services")
    entry = services.get(_GIW) if isinstance(services, dict) else None
    raw = entry.get("active_work") if isinstance(entry, dict) else None
    active_work = raw if isinstance(raw, dict) else {}
    return [
        {
            "occupant": "busy_status",
            "excluded_dispatch_id": exclude_dispatch_id,
            "foreign": _foreign_giw_rows(active_work, exclude=exclude_dispatch_id),
        }
    ]


def caller_dispatch_id() -> str | None:
    """cursor-sdk dispatch id of this process, when it runs inside one."""
    value = os.environ.get(_CALLER_DISPATCH_ENV, "").strip()
    return value or None


def observe_giw_occupants(
    busy: dict[str, Any],
    *,
    caller_dispatch: str | None,
    dry_run: bool = False,
    ledger_reader: Callable[[], list[Any]] | None = None,
) -> RefuseFinding | None:
    """Refuse when GIW has claimed occupants.

    Independent of ``manage_inflight_others`` and of whether GIW is a manage
    child: on execute, a seat running inside a cursor-sdk dispatch is itself
    a GIW occupant even when busy_status has not caught up. Dry-run with a
    caller id excludes that caller. Order on that branch: foreign rows, then
    unreadable when active_work or ``cursor_sdk_gate.holders`` (both lane
    lists) or ``live_writers`` is missing, else clear. ``ledger_reader`` is
    off unless a caller injects it; the reexec path passes none.
    """
    if caller_dispatch and dry_run:
        foreign = _foreign_giw_offenders(busy, exclude_dispatch_id=caller_dispatch)[0][
            "foreign"
        ]
        if ledger_reader is not None:
            foreign = _dedup_by_dispatch_id(
                [
                    *foreign,
                    *_ledger_reader_rows(ledger_reader(), exclude=caller_dispatch),
                ]
            )
        if foreign:
            return RefuseFinding(
                reason="giw_claimed_occupants",
                offenders=[
                    {
                        "occupant": "busy_status",
                        "excluded_dispatch_id": caller_dispatch,
                        "foreign": foreign,
                    }
                ],
            )
        active_work = _giw_active_work(busy)
        if active_work is None or not _dry_run_occupancy_readable(active_work):
            return RefuseFinding(
                reason="giw_occupancy_unreadable",
                offenders=[
                    {
                        "occupant": "busy_status",
                        "active_work_present": active_work is not None,
                    }
                ],
            )
        return None
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
    "giw_foreign_claimed_occupants",
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
