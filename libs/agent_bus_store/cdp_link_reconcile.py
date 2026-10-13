"""Watchdog sweep: terminalize cdp-generate links when Stargate has no open leg."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM

from .cdp_pointer_sweep import _failed_subject
from .db.connection import connect, now, write_connect
from .db.threads_atomic import terminate_dispatch
from .db.turns import insert_turn
from .events.lifecycle import emit_dispatch_orphaned
from .producer_projection import producer_liveness_grace
from .producer_witness import (
    CDP_GENERATE_PIPELINE_ID,
    CdpLegProbeResult,
    WitnessVerdict,
    probe_cdp_leg_status,
)

logger = logging.getLogger("agent-bus.cdp_link_reconcile")

CDP_LINK_RECONCILE_BATCH_LIMIT = 50
_PENDING_ABSENT = "pending_cdp_link:producer_journal_absent"
_PENDING_CLOSED = "pending_cdp_link:producer_closed_link_open"


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _age_seconds(linked_at: str) -> float:
    linked = _parse_ts(linked_at)
    if linked.tzinfo is None:
        linked = linked.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - linked).total_seconds())


def _grace_cutoff() -> str:
    cutoff = datetime.now(UTC) - producer_liveness_grace()
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_deferred(*, thread_id: str, execution_id: str, reason: str) -> None:
    with write_connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links "
            "SET liveness_probe_deferred_at = ?, liveness_probe_deferred_reason = ? "
            "WHERE thread_id = ? AND execution_id = ?",
            (now(), reason, thread_id, execution_id),
        )


def _clear_deferred(*, thread_id: str, execution_id: str) -> None:
    with write_connect() as conn:
        conn.execute(
            "UPDATE thread_dispatch_links "
            "SET liveness_probe_deferred_at = NULL, liveness_probe_deferred_reason = NULL "
            "WHERE thread_id = ? AND execution_id = ?",
            (thread_id, execution_id),
        )


def _emit_orphaned(link: dict[str, Any], *, reason: str) -> None:
    emit_dispatch_orphaned(
        execution_id=link["execution_id"],
        thread_id=link["thread_id"],
        pipeline_id=link["pipeline_id"],
        linked_at=link["linked_at"],
        age_s=_age_seconds(link["linked_at"]),
        reason=reason,
    )


def _post_cdp_failed_turn(
    *,
    thread_id: str,
    execution_id: str,
    caller_agent: str | None,
    body: str,
) -> None:
    recipient = caller_agent or "dispatch"
    insert_turn(
        thread=thread_id,
        from_agent=CDP_REPLY_FROM,
        to_agent=recipient,
        subject=_failed_subject(execution_id),
        body=body,
        after_turn=None,
    )


def _stamp_link_terminal(
    *,
    thread_id: str,
    execution_id: str,
    terminal_status: str,
) -> None:
    terminate_dispatch(
        thread_id=thread_id,
        terminal_status=terminal_status,
        execution_id=execution_id,
        fan_out=False,
    )


def _reconcile_one(link: dict[str, Any]) -> bool:
    execution_id = link["execution_id"]
    thread_id = link["thread_id"]
    probe = probe_cdp_leg_status(execution_id)

    with connect() as conn:
        row = conn.execute(
            "SELECT terminal_status, liveness_probe_deferred_reason "
            "FROM thread_dispatch_links WHERE thread_id = ? AND execution_id = ?",
            (thread_id, execution_id),
        ).fetchone()
    if row is None or row["terminal_status"] is not None:
        return False
    prior_pending = row["liveness_probe_deferred_reason"]

    if probe.verdict is WitnessVerdict.LIVE:
        _clear_deferred(thread_id=thread_id, execution_id=execution_id)
        return False

    if probe.verdict is WitnessVerdict.UNKNOWN:
        if probe.reason == "leg_closed":
            return _reconcile_closed_leg(
                link,
                probe=probe,
                prior_pending=prior_pending,
            )
        _stamp_deferred(
            thread_id=thread_id,
            execution_id=execution_id,
            reason=probe.reason,
        )
        return False

    assert probe.verdict is WitnessVerdict.ABSENT
    if prior_pending != _PENDING_ABSENT:
        _stamp_deferred(
            thread_id=thread_id,
            execution_id=execution_id,
            reason=_PENDING_ABSENT,
        )
        return False

    _clear_deferred(thread_id=thread_id, execution_id=execution_id)
    _post_cdp_failed_turn(
        thread_id=thread_id,
        execution_id=execution_id,
        caller_agent=link.get("caller_agent"),
        body=(
            f"CDP generate link orphaned (execution_id={execution_id}). "
            "Stargate ledger has no in-flight leg after grace."
        ),
    )
    _stamp_link_terminal(
        thread_id=thread_id,
        execution_id=execution_id,
        terminal_status="failed",
    )
    _emit_orphaned(link, reason="producer_journal_absent")
    return True


def _reconcile_closed_leg(
    link: dict[str, Any],
    *,
    probe: CdpLegProbeResult,
    prior_pending: str | None,
) -> bool:
    payload = probe.payload or {}
    terminal = payload.get("terminal")
    delivered = payload.get("delivered")
    execution_id = link["execution_id"]
    thread_id = link["thread_id"]

    if terminal is None:
        _stamp_deferred(
            thread_id=thread_id,
            execution_id=execution_id,
            reason=f"cdp_closed_no_terminal:{probe.reason}",
        )
        _emit_orphaned(link, reason="producer_closed_link_open")
        return False

    if prior_pending != _PENDING_CLOSED:
        _stamp_deferred(
            thread_id=thread_id,
            execution_id=execution_id,
            reason=_PENDING_CLOSED,
        )
        return False

    _clear_deferred(thread_id=thread_id, execution_id=execution_id)
    status = str(terminal)
    if status not in ("completed", "failed"):
        logger.warning(
            "cdp_link_reconcile: ignore malformed terminal=%r execution=%s",
            terminal,
            execution_id[:8],
        )
        return False

    if status == "failed" and not delivered:
        _post_cdp_failed_turn(
            thread_id=thread_id,
            execution_id=execution_id,
            caller_agent=link.get("caller_agent"),
            body=(
                f"CDP generate closed without delivery (execution_id={execution_id})."
            ),
        )
    _stamp_link_terminal(
        thread_id=thread_id,
        execution_id=execution_id,
        terminal_status=status,
    )
    _emit_orphaned(link, reason="producer_closed_link_open")
    return True


def reconcile_cdp_dispatch_links(*, batch_limit: int = CDP_LINK_RECONCILE_BATCH_LIMIT) -> int:
    """Stamp null-terminal cdp-generate links after two-pass Stargate evidence."""
    cutoff = _grace_cutoff()
    with connect() as conn:
        rows = conn.execute(
            "SELECT thread_id, execution_id, pipeline_id, linked_at, caller_agent "
            "FROM thread_dispatch_links "
            "WHERE pipeline_id = ? AND terminal_status IS NULL "
            "  AND replace(replace(linked_at, 'T', ' '), 'Z', '') < "
            "      replace(replace(?, 'T', ' '), 'Z', '') "
            "ORDER BY linked_at ASC LIMIT ?",
            (CDP_GENERATE_PIPELINE_ID, cutoff, batch_limit),
        ).fetchall()

    stamped = 0
    for row in rows:
        if _reconcile_one(dict(row)):
            stamped += 1
    return stamped
