"""Refuse / require observations for guarded manage reexec.

All refuse conditions are read from outside the manage PID. Restart intents and
open restart windows come from ``~/.gateway/restart-intents.db``. Process
inflight and pause drain come from manage.sock while it is still up.

Wire note: ``charter_hold_status`` returns ``pause_drain_clear``; MCP/docs still
say ``safe_to_quit``. This package keys the wire field only.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.restart_window_store import (
    window_status_view,
)

# busy_status snapshots manage_inflight AFTER enter_request for the observing
# call itself (api_server.py enter→dispatch→leave). A literal ``> 0`` check is
# therefore always true when read via busy_status and would be decorative.
# Refuse when OTHER in-flight handlers exist: observed_count > 1.
_SELF_BUSY_STATUS_INFLIGHT = 1

BusyStatusFn = Callable[[], dict[str, Any]]
HoldStatusFn = Callable[[], dict[str, Any]]


def resolve_gateway_dir(*, manage_pid: int | None = None) -> Path:
    """Resolve the host manage gateway dir from outside a possibly-shifted HOME.

    cursor-sdk seats often have HOME under the dispatch tree; manage itself runs
    with the operator HOME (``/home/io``). Prefer ``GATEWAY_DIR`` env, then the
    live manage process environ via ``/proc/<pid>/environ``, then ``Path.home()``.
    """
    env_dir = os.environ.get("GATEWAY_DIR")
    if env_dir:
        return Path(env_dir)
    if manage_pid is not None:
        try:
            raw = Path(f"/proc/{manage_pid}/environ").read_bytes()
        except OSError:
            raw = b""
        for item in raw.split(b"\0"):
            if item.startswith(b"GATEWAY_DIR="):
                return Path(item.split(b"=", 1)[1].decode())
            if item.startswith(b"HOME="):
                return Path(item.split(b"=", 1)[1].decode()) / ".gateway"
    return Path.home() / ".gateway"


def default_intent_db(*, manage_pid: int | None = None) -> Path:
    """Return the host manage restart-intents SQLite path under gateway dir."""
    return resolve_gateway_dir(manage_pid=manage_pid) / "restart-intents.db"


@dataclass(slots=True)
class RefuseFinding:
    """One loud refuse reason plus the offending rows/payloads."""

    reason: str
    offenders: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class CheckReport:
    """Aggregate refuse/require observation for dry-run or execute."""

    refused: bool
    findings: list[RefuseFinding] = field(default_factory=list)
    manage_inflight_raw: int | None = None
    manage_inflight_others: int | None = None
    activities: list[str] = field(default_factory=list)
    pause_drain_clear: bool | None = None
    held: bool | None = None
    busy_status: dict[str, Any] | None = None
    hold_status: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "refused": self.refused,
            "findings": [
                {"reason": f.reason, "offenders": f.offenders} for f in self.findings
            ],
            "manage_inflight_raw": self.manage_inflight_raw,
            "manage_inflight_others": self.manage_inflight_others,
            "activities": list(self.activities),
            "pause_drain_clear": self.pause_drain_clear,
            "held": self.held,
        }


def observe_nonterminal_intents(
    store: RestartIntentStore | None = None,
    *,
    db_path: Path | None = None,
    manage_pid: int | None = None,
) -> RefuseFinding | None:
    """Refuse loudly when any non-terminal restart intent row still exists."""
    store = store or RestartIntentStore(
        db_path=db_path or default_intent_db(manage_pid=manage_pid)
    )
    pending = store.pending_intents()
    if not pending:
        return None
    offenders = [
        {
            "intent_id": i.intent_id,
            "service": i.service,
            "action": i.action,
            "status": i.status,
            "deadline_at": i.deadline_at,
            "reason": i.reason,
        }
        for i in pending
    ]
    return RefuseFinding(reason="nonterminal_restart_intent", offenders=offenders)


def observe_open_restart_windows(
    store: RestartIntentStore | None = None,
    *,
    db_path: Path | None = None,
    manage_pid: int | None = None,
    now: datetime | None = None,
) -> RefuseFinding | None:
    """Refuse loudly when any operator restart window row is still open."""
    store = store or RestartIntentStore(
        db_path=db_path or default_intent_db(manage_pid=manage_pid)
    )
    now = now or datetime.now(UTC)
    store.sweep_expired_windows(now=now)
    open_windows = store.active_windows()
    if not open_windows:
        return None
    offenders = [dict(window_status_view(w, now=now)) for w in open_windows]
    return RefuseFinding(reason="open_restart_window", offenders=offenders)


def observe_manage_inflight(busy: dict[str, Any]) -> tuple[RefuseFinding | None, int, int, list[str]]:
    """Refuse when other manage.sock handlers or named activities are live.

    Returns ``(finding|None, raw_inflight, others_inflight, activities)``.
    ``others_inflight`` subtracts the observing busy_status call — see module
    constant ``_SELF_BUSY_STATUS_INFLIGHT``.
    """
    process = busy.get("process") if isinstance(busy.get("process"), dict) else {}
    raw = int(process.get("manage_inflight") or 0)
    activities = [str(a) for a in (process.get("activities") or [])]
    others = max(0, raw - _SELF_BUSY_STATUS_INFLIGHT)
    if others <= 0 and not activities:
        return None, raw, others, activities
    offenders: list[dict[str, Any]] = [
        {
            "manage_inflight_raw": raw,
            "manage_inflight_others": others,
            "activities": activities,
            "self_observation_credit": _SELF_BUSY_STATUS_INFLIGHT,
        }
    ]
    return (
        RefuseFinding(reason="manage_inflight_or_activities", offenders=offenders),
        raw,
        others,
        activities,
    )


def observe_drain_clear(hold: dict[str, Any]) -> RefuseFinding | None:
    """Refuse when wire ``pause_drain_clear`` is not true.

    Docs/MCP still mention ``safe_to_quit``; that key is absent on the wire —
    key ``pause_drain_clear`` only (docs disagree).
    """
    # Docs say safe_to_quit; wire returns pause_drain_clear — key the wire.
    clear = hold.get("pause_drain_clear")
    if clear is True:
        return None
    return RefuseFinding(
        reason="drain_not_clear",
        offenders=[
            {
                "pause_drain_clear": clear,
                "held": hold.get("held"),
                "tick_in_flight": hold.get("tick_in_flight"),
                "live_charter_shaped_dispatches": hold.get(
                    "live_charter_shaped_dispatches"
                ),
                "giw_charter_probe_status": hold.get("giw_charter_probe_status"),
                "safe_to_quit_on_wire": hold.get("safe_to_quit"),  # expected absent
            }
        ],
    )


def collect_refuse_report(
    *,
    busy_status_fn: BusyStatusFn,
    hold_status_fn: HoldStatusFn | None = None,
    store: RestartIntentStore | None = None,
    db_path: Path | None = None,
    manage_pid: int | None = None,
    require_drain_clear: bool = True,
) -> CheckReport:
    """Aggregate every external refuse/require observation into one report."""
    findings: list[RefuseFinding] = []
    intent_finding = observe_nonterminal_intents(
        store, db_path=db_path, manage_pid=manage_pid
    )
    if intent_finding is not None:
        findings.append(intent_finding)
    window_finding = observe_open_restart_windows(
        store, db_path=db_path, manage_pid=manage_pid
    )
    if window_finding is not None:
        findings.append(window_finding)

    busy = busy_status_fn()
    if busy.get("status") == "error":
        findings.append(
            RefuseFinding(
                reason="busy_status_unobservable",
                offenders=[busy],
            )
        )
        return CheckReport(
            refused=True,
            findings=findings,
            busy_status=busy,
        )

    inflight_finding, raw, others, activities = observe_manage_inflight(busy)
    if inflight_finding is not None:
        findings.append(inflight_finding)

    hold: dict[str, Any] | None = None
    pause_clear: bool | None = None
    held: bool | None = None
    if require_drain_clear:
        # Prefer dedicated hold_status; fall back to busy_status.charter_hold.
        if hold_status_fn is not None:
            hold = hold_status_fn()
            if hold.get("status") == "error":
                findings.append(
                    RefuseFinding(reason="hold_status_unobservable", offenders=[hold])
                )
                return CheckReport(
                    refused=True,
                    findings=findings,
                    manage_inflight_raw=raw,
                    manage_inflight_others=others,
                    activities=activities,
                    busy_status=busy,
                    hold_status=hold,
                )
        else:
            hold = busy.get("charter_hold") if isinstance(busy.get("charter_hold"), dict) else {}
        pause_clear = bool(hold.get("pause_drain_clear")) if hold else None
        held = bool(hold.get("held")) if hold else None
        drain_finding = observe_drain_clear(hold or {})
        if drain_finding is not None:
            findings.append(drain_finding)

    return CheckReport(
        refused=bool(findings),
        findings=findings,
        manage_inflight_raw=raw,
        manage_inflight_others=others,
        activities=activities,
        pause_drain_clear=pause_clear,
        held=held,
        busy_status=busy,
        hold_status=hold,
    )
