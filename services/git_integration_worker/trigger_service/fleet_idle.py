"""Injectable fleet idle probe for the ``fleet_idle`` predicate."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from universal_logging import get_logger

logger = get_logger(__name__)

# Root statuses that block tick_empty in narrow (default) mode.
_CHARTER_BUSY_ROOT_NARROW = (
    "ADMITTED",
    "HARVEST_WAIT",
    "CONSULT_ADMITTED",
)

# Strict mode adds queued/deferred root consult states (legacy behaviour).
_CHARTER_BUSY_ROOT_STRICT_EXTRA = (
    "CONSULT_QUEUED",
    "CONSULT_DEFERRED",
)

_CHARTER_BUSY_ROOT_STRICT = _CHARTER_BUSY_ROOT_NARROW + _CHARTER_BUSY_ROOT_STRICT_EXTRA

_CONSULT_BUSY_NARROW = ("admitted", "running")
_CONSULT_BUSY_STRICT = ("queued", "admitted", "running")

_last_busy_monotonic: float = 0.0
_idle_since_monotonic: float | None = None
_pass_snapshot: FleetIdleSnapshot | None = None


class FleetVerdict(StrEnum):
    """Three-valued fleet idle probe result — fail-closed without masquerading."""

    IDLE = "idle"
    BUSY = "busy"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class FleetIdleSnapshot:
    """Instantaneous fleet signals — grace applied in ``eval_fleet_idle``."""

    verdict: FleetVerdict
    dispatch_idle: bool
    tick_empty: bool
    cursor_auto_idle: bool
    dispatch_undetermined: bool = False
    tick_undetermined: bool = False
    tick_empty_strict: bool = True


class FleetIdleReader(Protocol):
    """Narrow interface so unit tests avoid live fleet sqlite."""

    def read(self) -> FleetIdleSnapshot: ...


def reset_grace_tracker() -> None:
    """Test hook — reset grace clock."""
    global _last_busy_monotonic, _idle_since_monotonic
    _last_busy_monotonic = time.monotonic()
    _idle_since_monotonic = None


def begin_idle_pass() -> None:
    """Clear per-pass memoization at the start of ``run_trigger_pass``."""
    global _pass_snapshot
    _pass_snapshot = None


def read_fleet_idle_memoized(
    reader: FleetIdleReader | None = None,
) -> FleetIdleSnapshot:
    """Read fleet idle once per fire pass (AC6)."""
    global _pass_snapshot
    if _pass_snapshot is not None:
        return _pass_snapshot
    probe = reader or DefaultFleetIdleReader()
    _pass_snapshot = probe.read()
    return _pass_snapshot


def eval_fleet_idle(
    snapshot: FleetIdleSnapshot,
    args: dict[str, Any],
    *,
    now_monotonic: float | None = None,
) -> bool:
    """True when verdict is idle and grace (if any) has elapsed."""
    global _last_busy_monotonic, _idle_since_monotonic
    block_on_queued = bool(args.get("block_on_queued_consults", False))
    effective_tick_empty = (
        snapshot.tick_empty_strict if block_on_queued else snapshot.tick_empty
    )
    verdict = _compose_verdict(
        dispatch_idle=snapshot.dispatch_idle,
        dispatch_undetermined=snapshot.dispatch_undetermined,
        tick_empty=effective_tick_empty,
        tick_undetermined=snapshot.tick_undetermined,
        cursor_auto_idle=snapshot.cursor_auto_idle,
    )
    if verdict is not FleetVerdict.IDLE:
        now = now_monotonic if now_monotonic is not None else time.monotonic()
        _last_busy_monotonic = now
        _idle_since_monotonic = None
        return False

    grace_s = max(0, int(args.get("grace_s", 0)))
    now = now_monotonic if now_monotonic is not None else time.monotonic()

    if grace_s <= 0:
        return True
    if _idle_since_monotonic is None:
        _idle_since_monotonic = now
    return (now - _idle_since_monotonic) >= grace_s


class DefaultFleetIdleReader:
    """Production reader: dispatch ledger + cursor-auto queue + charter ledger."""

    def read(self) -> FleetIdleSnapshot:
        dispatch_idle, dispatch_undetermined = _dispatch_idle()
        tick_empty, tick_empty_strict, tick_undetermined = _charter_tick_empty()
        auto_idle = _cursor_auto_idle()
        verdict = _compose_verdict(
            dispatch_idle=dispatch_idle,
            dispatch_undetermined=dispatch_undetermined,
            tick_empty=tick_empty,
            tick_undetermined=tick_undetermined,
            cursor_auto_idle=auto_idle,
        )
        return FleetIdleSnapshot(
            verdict=verdict,
            dispatch_idle=dispatch_idle,
            tick_empty=tick_empty,
            cursor_auto_idle=auto_idle,
            dispatch_undetermined=dispatch_undetermined,
            tick_undetermined=tick_undetermined,
            tick_empty_strict=tick_empty_strict,
        )


def _compose_verdict(
    *,
    dispatch_idle: bool,
    dispatch_undetermined: bool,
    tick_empty: bool,
    tick_undetermined: bool,
    cursor_auto_idle: bool,
) -> FleetVerdict:
    if dispatch_undetermined or tick_undetermined:
        return FleetVerdict.UNDETERMINED
    if not dispatch_idle or not tick_empty or not cursor_auto_idle:
        return FleetVerdict.BUSY
    return FleetVerdict.IDLE


def _dispatch_idle() -> tuple[bool, bool]:
    try:
        from services.git_integration_worker.cursor_dispatch_ledger import (
            CursorDispatchLedger,
        )

        ledger = CursorDispatchLedger.instance()
        snap = ledger.lease_snapshot()
        if snap.get("holder_dispatch_id"):
            return False, False
        if int(snap.get("queue_depth") or 0) > 0:
            return False, False
        active = ledger.active_snapshot()
        return int(active.get("running") or 0) == 0, False
    except Exception:
        logger.warning("dispatch idle probe failed — undetermined", exc_info=True)
        return False, True


def _cursor_auto_idle() -> bool:
    from services.git_integration_worker.cursor_auto.queue import get_queue

    claimed = int(get_queue().snapshot().get("claimed") or 0)
    return claimed == 0


def _charter_tick_empty() -> tuple[bool, bool, bool]:
    """Return (narrow_empty, strict_empty, undetermined)."""
    try:
        from libs.charter_runner_store.db import open_ledger_db

        conn = open_ledger_db()
        try:
            narrow_empty = _root_ledger_empty(conn, _CHARTER_BUSY_ROOT_NARROW)
            strict_empty = narrow_empty
            if narrow_empty:
                strict_empty = _root_ledger_empty(conn, _CHARTER_BUSY_ROOT_STRICT)
            if not narrow_empty:
                return False, False, False
            if not strict_empty:
                return True, False, False
            narrow_consult = _consult_queue_empty(conn, _CONSULT_BUSY_NARROW)
            strict_consult = _consult_queue_empty(conn, _CONSULT_BUSY_STRICT)
            return narrow_consult, strict_consult, False
        finally:
            conn.close()
    except Exception:
        logger.warning(
            "charter tick probe failed — undetermined (fail-closed)",
            exc_info=True,
        )
        return False, False, True


def _root_ledger_empty(conn, statuses: tuple[str, ...]) -> bool:
    placeholders = ",".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM root_ledger WHERE status IN ({placeholders})",
        statuses,
    ).fetchone()
    return not row or int(row["n"]) == 0


def _consult_queue_empty(conn, statuses: tuple[str, ...]) -> bool:
    placeholders = ",".join("?" for _ in statuses)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM consult_queue WHERE status IN ({placeholders})",
        statuses,
    ).fetchone()
    return not row or int(row["n"]) == 0
