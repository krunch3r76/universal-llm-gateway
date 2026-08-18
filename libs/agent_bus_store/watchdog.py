"""TTL backstop watchdog for bus thread lifecycle.

Runs as a background asyncio task. Four sweep paths:

  1. pending → abandoned   — thread created with intent but never admitted
  2. admitted → abandoned  — dispatch registered but first turn never posted
  3. active → abandoned    — no activity within the long-TTL window (dispatch-
                             originated threads only; user-driven active threads
                             are caller-managed and not reaped here)
  4. quiet-with-WIP soft-actuate — seat silent with open WIP (A′ / arc 6885);
     alarm row + event + lane turn (does not abandon the thread)

Each sweep pass is synchronous (SQLite I/O); called from the async loop directly
(no executor needed — infrequent, fast).

TTL accuracy: each state transition bumps `threads.updated_at` via
`_transition_lifecycle_state` using `now()` (ISO-8601 with ``T`` and ``Z``).
Cutoff strings use the same form. Lexical compare still normalizes ISO vs
sqlite ``datetime()`` so mixed on-disk rows remain ordered. Default sweep
interval is 300s; worst-case reap lag = TTL + one sweep period (~5 min extra).
Admitted reap additionally probes the GIW holder and fail-closes on DEFER.

∀ sweep failure: emit mcp.agentbus.watchdog.sweep.failed; never crash the task.
∀ reap attempt: re-check lifecycle state inside the transaction (TOCTOU guard).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from .db.connection import connect, now
from .db.lifecycle import _transition_lifecycle_state
from .events.lifecycle import emit_thread_abandoned, emit_watchdog_sweep_failed
from .sdk_liveness import (
    LivenessVerdict,
    ProbeResult,
    evaluate_link_liveness,
    probe_dispatch_status,
)

# ── Configuration ─────────────────────────────────────────────────────────────

_PENDING_TTL: int = int(os.getenv("AGENT_BUS_WATCHDOG_PENDING_TTL", "1800"))
_ADMITTED_TTL: int = int(os.getenv("AGENT_BUS_WATCHDOG_ADMITTED_TTL", "3600"))
_ACTIVE_ABANDON_TTL: int = int(
    os.getenv("AGENT_BUS_WATCHDOG_ACTIVE_ABANDON_TTL", "172800")
)
_SWEEP_INTERVAL: int = int(os.getenv("AGENT_BUS_WATCHDOG_SWEEP_INTERVAL", "300"))

ReapReason = Literal[
    "pending_ttl_exceeded",
    "admitted_ttl_exceeded",
    "all_terminal_no_delivery",
    "tracker_expired",
]


# ── Reap helpers ──────────────────────────────────────────────────────────────


def _reap_single(
    thread_id: str,
    *,
    expected_state: str,
    reason: ReapReason,
) -> bool:
    """Attempt to reap one thread.

    Opens its own transaction. Returns True when the thread was abandoned,
    False when it was already advanced (TOCTOU guard fired).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT bus_lifecycle_state FROM threads WHERE id = ?", (thread_id,)
        ).fetchone()
        if row is None or row["bus_lifecycle_state"] != expected_state:
            return False

        links = conn.execute(
            "SELECT terminal_status, delivery_at FROM thread_dispatch_links "
            "WHERE thread_id = ?",
            (thread_id,),
        ).fetchall()

        _transition_lifecycle_state(conn, thread_id, "abandoned", "watchdog_reap")

        ts = now()
        conn.execute(
            "UPDATE threads SET status = 'closed', updated_at = ? WHERE id = ?",
            (ts, thread_id),
        )

    emit_thread_abandoned(
        thread=thread_id,
        reason=reason,
        link_count=len(links),
        terminal_count=sum(1 for lnk in links if lnk["terminal_status"] is not None),
        delivered_count=sum(1 for lnk in links if lnk["delivery_at"] is not None),
    )
    return True


def _ts_older_than(column: str) -> str:
    """SQL predicate: ``column`` is older than bound cutoff.

    ``now()`` stores ``YYYY-MM-DDTHH:MM:SSZ``; sqlite ``datetime('now')``
    stores a space and no ``Z``. Lexicographic compare of those forms is
    inverted (``T`` > space), so the TTL select must normalize both sides.
    """
    return (
        f"replace(replace({column}, 'T', ' '), 'Z', '') < "
        "replace(replace(?, 'T', ' '), 'Z', '')"
    )


def _reap_pending(cutoff: str) -> None:
    """Abandon pending threads older than PENDING_TTL.

    `cutoff` is an ISO-8601 timestamp: threads with created_at < cutoff qualify.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM threads "
            f"WHERE bus_lifecycle_state = 'pending' AND {_ts_older_than('created_at')}",
            (cutoff,),
        ).fetchall()
    for row in rows:
        _reap_single(row["id"], expected_state="pending", reason="pending_ttl_exceeded")


def _holder_blocks_admitted_reap(
    thread_id: str,
    *,
    probe_fn: Callable[[str], ProbeResult],
) -> bool:
    """True when the GIW holder is live or the probe is uncertain (fail-closed)."""
    with connect() as conn:
        link = conn.execute(
            "SELECT execution_id FROM thread_dispatch_links "
            "WHERE thread_id = ? ORDER BY linked_at DESC LIMIT 1",
            (thread_id,),
        ).fetchone()
    execution_id = None if link is None else link["execution_id"]
    verdict, _reason, _terminal = evaluate_link_liveness(
        thread_id=thread_id,
        link_execution_id=execution_id,
        probe_fn=probe_fn,
    )
    return verdict in (LivenessVerdict.SKIP_LIVE, LivenessVerdict.DEFER)


def _reap_admitted(
    cutoff: str,
    *,
    probe_fn: Callable[[str], ProbeResult] = probe_dispatch_status,
) -> None:
    """Abandon admitted threads with no activity for ADMITTED_TTL.

    Quiet ``threads.updated_at`` is not death. Probe the GIW holder before
    reap; ``SKIP_LIVE`` and ``DEFER`` skip. ``parked_waiting`` is live
    (see sdk_liveness). Cursor-sdk generate often posts its pointer while
    pending, then admit — those threads stay ``admitted`` with turn_count≥1.

    ``bump_heartbeat`` does not bump bus ``updated_at``; the holder probe is
    the liveness authority, not a second write into the TTL clock.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM threads "
            f"WHERE bus_lifecycle_state = 'admitted' AND {_ts_older_than('updated_at')}",
            (cutoff,),
        ).fetchall()
    for row in rows:
        if _holder_blocks_admitted_reap(row["id"], probe_fn=probe_fn):
            continue
        _reap_single(
            row["id"], expected_state="admitted", reason="admitted_ttl_exceeded"
        )


def _reap_active(cutoff: str) -> None:
    """Abandon stale active threads that originated from a pipeline dispatch.

    Restricted to dispatch-originated threads (EXISTS on thread_dispatch_links)
    so user-driven active conversations are never touched. Uses `updated_at` as
    the activity proxy; a finer-grained check (all links terminal, no delivery)
    requires `terminal_status` population — deferred to a separate ticket.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.id FROM threads t "
            f"WHERE t.bus_lifecycle_state = 'active' AND {_ts_older_than('t.updated_at')} "
            "  AND EXISTS ("
            "    SELECT 1 FROM thread_dispatch_links WHERE thread_id = t.id"
            "  )",
            (cutoff,),
        ).fetchall()
    for row in rows:
        _reap_single(
            row["id"], expected_state="active", reason="all_terminal_no_delivery"
        )


def _cutoff_for_ttl(ttl_seconds: int) -> str:
    """Return an ISO-8601 timestamp `ttl_seconds` in the past.

    Matches ``now()`` (``YYYY-MM-DDTHH:MM:SSZ``). Sqlite ``datetime('now')``
    is a different string form and must not be compared to ``now()`` lexically.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=ttl_seconds)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Main sweep + async task ───────────────────────────────────────────────────


def _sweep() -> None:
    """Single synchronous sweep pass — called from the async task."""
    _reap_pending(_cutoff_for_ttl(_PENDING_TTL))
    _reap_admitted(_cutoff_for_ttl(_ADMITTED_TTL))
    _reap_active(_cutoff_for_ttl(_ACTIVE_ABANDON_TTL))
    from .quiet_sweep import sweep_quiet_with_wip
    from .reconcile import reconcile_orphaned_dispatches

    reconcile_orphaned_dispatches()
    sweep_quiet_with_wip()


async def run_watchdog() -> None:
    """TTL reaper loop. Runs until task is cancelled.

    CancelledError propagates from `asyncio.sleep` — no suppress needed.
    """
    while True:
        await asyncio.sleep(_SWEEP_INTERVAL)
        try:
            _sweep()
        except Exception as exc:
            emit_watchdog_sweep_failed(error=str(exc)[:500])
