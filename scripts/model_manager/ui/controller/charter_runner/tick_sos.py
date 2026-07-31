"""Automatic charter-tick SOS — pager + cursor-auto note + optional CDP heal.

Doctrine: ``decision:tick-heal-cdp-operator-default``. When the tick
silent-starves, do not wait for IDE babysitting — claim once, page an SOS
(minimum), leave details on the bus for cursor-auto, and auto-admit a CDP
operator-proxy heal via ``team_dispatch(model=cdp/opus-5, purpose=operator-proxy)``
when ``CHARTER_TICK_SOS_CDP`` is enabled.
"""

from __future__ import annotations

import os
from typing import Any

from pager_notify.sos import claim_tick_sos, notify_tick_sos
from universal_logging import get_logger

from .tick_sos_cdp_heal import submit_cdp_heal as _submit_cdp_heal

logger = get_logger(__name__)

# Consecutive identical pathological observations before fire.
_DEFAULT_THRESHOLD = 3
# Sticky ADMITTED / CONSULT_ADMITTED NOOP needs a longer fuse (looks like WIP).
_DEFAULT_STICKY_THRESHOLD = 9  # ~3 min at 20s tick

# Skip reasons that are never legal standing waits.
_PATHALOGICAL_SKIPS = frozenset(
    {
        "executor_mismatch",
        "consult_pending_empty_hopper",
        "sticky_admitted",
    }
)

_counter: dict[str, tuple[str, int]] = {}


def _threshold(reason: str) -> int:
    if reason == "sticky_admitted":
        raw = os.environ.get(
            "CHARTER_TICK_SOS_STICKY_THRESHOLD",
            str(_DEFAULT_STICKY_THRESHOLD),
        )
    else:
        raw = os.environ.get("CHARTER_TICK_SOS_THRESHOLD", str(_DEFAULT_THRESHOLD))
    try:
        return max(1, int(raw))
    except ValueError:
        return _DEFAULT_STICKY_THRESHOLD if reason == "sticky_admitted" else _DEFAULT_THRESHOLD


def tick_heal_enabled() -> bool:
    """Master heal actuator gate — pager, bus note, CDP (default off until revisit)."""
    raw = os.environ.get("CHARTER_TICK_HEAL_ENABLED", "0").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _sos_cdp_enabled() -> bool:
    raw = os.environ.get("CHARTER_TICK_SOS_CDP", "0").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def clear_tick_sos(root_id: str) -> None:
    """Reset consecutive counter after admit / healthy progress."""
    _counter.pop(str(root_id), None)


def classify_skip_for_sos(
    *,
    skipped_reason: str | None,
    consult_pending: bool = False,
    ledger_status: str | None = None,
    old_decision_label: str | None = None,
) -> str | None:
    """Map a tick observation to an SOS reason, or None if not pathological."""
    reason = (skipped_reason or "").strip()
    if reason == "empty_hopper" and consult_pending:
        return "consult_pending_empty_hopper"
    if reason in _PATHALOGICAL_SKIPS:
        return reason
    if reason in {"dormant", "empty_hopper", "no_gated_pickup", "exhausted_hopper"}:
        return None
    status = (ledger_status or "").upper()
    label = (old_decision_label or "").upper()
    if (
        not reason
        and label == "NOOP"
        and status in {"ADMITTED", "CONSULT_ADMITTED"}
    ):
        return "sticky_admitted"
    return None


def note_observation(root_id: str, reason: str) -> int:
    """Bump consecutive count for ``root:reason``; reset on reason change.

    Returns the new consecutive count for this reason.
    """
    key = str(root_id)
    prior = _counter.get(key)
    if prior is None or prior[0] != reason:
        _counter[key] = (reason, 1)
        return 1
    count = prior[1] + 1
    _counter[key] = (reason, count)
    return count


def _ledger_status(root_id: str) -> str | None:
    try:
        from .root_ledger import load_root, open_default_ledger

        conn = open_default_ledger()
        try:
            row = load_root(conn, root_id)
        finally:
            conn.close()
        if row is None:
            return None
        return row.status.value
    except Exception:  # noqa: BLE001 — SOS must not abort tick
        logger.exception("tick SOS ledger read failed root=%s", root_id)
        return None


async def fire_episode_actuator(
    root_id: str,
    *,
    fire_attempt_outcome,
    fire_attempt_reason: str,
    worker_thread: str | None = None,
    refired: bool = False,
) -> dict[str, Any] | None:
    """Actuator behind episode open — pager + bus + optional CDP (not classifier SoT)."""
    from .root_health import FireAttemptOutcome

    if not tick_heal_enabled():
        logger.info("tick heal disabled root=%s (CHARTER_TICK_HEAL_ENABLED)", root_id)
        return None

    outcome = fire_attempt_outcome
    reason = (fire_attempt_reason or "").strip() or (
        outcome.value if isinstance(outcome, FireAttemptOutcome) else str(outcome or "")
    ).strip()
    if not reason:
        logger.warning("tick SOS refused blank reason root=%s", root_id)
        return None
    if refired:
        logger.info(
            "tick SOS actuator skipped on refire root=%s reason=%s",
            root_id,
            reason,
        )
        return None
    if not claim_tick_sos(root_id, reason):
        return None

    detail = reason
    if outcome == FireAttemptOutcome.FIRED_BOOKKEEPING_FAILED:
        detail = (
            f"{reason}; worker={worker_thread or 'unknown'} — "
            "pointer/harvest heal only; do NOT re-fire window"
        )

    result: dict[str, Any] = {
        "root_id": root_id,
        "reason": reason,
        "fire_attempt_outcome": outcome.value if outcome else None,
        "refired": refired,
        "paged": False,
        "bus_noted": False,
        "cdp_execution_id": None,
    }
    try:
        result["paged"] = await notify_tick_sos(
            root_id=root_id,
            reason=reason,
            detail=detail,
            consecutive=1,
        )
    except Exception:  # noqa: BLE001
        logger.exception("tick SOS pager failed root=%s", root_id)

    try:
        result["bus_noted"] = await _post_cursor_auto_note(
            root_id,
            reason=reason,
            consecutive=1,
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        logger.exception("tick SOS bus note failed root=%s", root_id)

    if _sos_cdp_enabled():
        try:
            result["cdp_execution_id"] = await _submit_cdp_heal(
                root_id,
                reason=reason,
                consecutive=1,
                detail=detail,
                fire_attempt_outcome=outcome,
                worker_thread=worker_thread,
            )
        except Exception:  # noqa: BLE001
            logger.exception("tick SOS CDP submit failed root=%s", root_id)

    logger.warning(
        "charter-tick SOS fired root=%s outcome=%s refired=%s paged=%s cdp=%s",
        root_id,
        outcome,
        refired,
        result["paged"],
        result["cdp_execution_id"],
    )
    return result


async def observe_kernel_outcome(
    root_id: str,
    *,
    skipped_reason: str | None,
    old_decision_label: str,
    admitted: bool,
    consult_pending: bool = False,
    detail: str = "",
) -> dict[str, Any] | None:
    """Host hook — clear on admit; else maybe fire SOS."""
    if admitted:
        clear_tick_sos(root_id)
        from .tick_sos_liveness import verdict_for_skip

        verdict_for_skip(
            sos_reason=None,
            skipped_reason=skipped_reason,
            root_id=root_id,
            giw_payload=None,
            admitted=True,
        )
        return None
    return await maybe_fire_tick_sos(
        root_id,
        skipped_reason=skipped_reason,
        consult_pending=consult_pending,
        ledger_status=_ledger_status(root_id),
        old_decision_label=old_decision_label,
        detail=detail,
        admitted=False,
    )


async def maybe_fire_tick_sos(
    root_id: str,
    *,
    skipped_reason: str | None = None,
    consult_pending: bool = False,
    ledger_status: str | None = None,
    old_decision_label: str | None = None,
    detail: str = "",
    admitted: bool = False,
    giw_payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Observe one root outcome; fire SOS+dispatch when threshold + claim clear.

    Returns a small result dict when fired, else None.
    """
    from .giw_live_hold import fetch_giw_active_work_payload
    from .telemetry import emit_root_skip_observed
    from .tick_sos_liveness import verdict_for_skip

    reason = classify_skip_for_sos(
        skipped_reason=skipped_reason,
        consult_pending=consult_pending,
        ledger_status=ledger_status,
        old_decision_label=old_decision_label,
    )
    payload = giw_payload
    if payload is None and not admitted:
        try:
            read = await fetch_giw_active_work_payload()
            payload = read.as_dict
        except Exception:  # noqa: BLE001 — SOS must not abort tick
            logger.exception("tick SOS GIW probe failed root=%s", root_id)
            payload = None

    verdict = verdict_for_skip(
        sos_reason=reason,
        skipped_reason=skipped_reason,
        root_id=root_id,
        giw_payload=payload,
        admitted=admitted,
    )
    consecutive = 0
    if reason is not None and not verdict.suppress_count_escalate:
        consecutive = note_observation(root_id, reason)
    elif reason is None or verdict.suppress_count_escalate:
        clear_tick_sos(root_id)

    try:
        await emit_root_skip_observed(
            root=root_id,
            skipped_reason=skipped_reason,
            sos_reason=reason,
            sticky_backing=verdict.sticky_backing,
            holder_dispatch_id=verdict.holder_dispatch_id,
            skip_count=consecutive
            or (verdict.age.observation_count if verdict.age else 0),
            holder_age_s=verdict.age.age_s if verdict.age else None,
        )
    except Exception:  # noqa: BLE001 — observation must not abort SOS
        logger.exception("tick SOS root_skip_observed emit failed root=%s", root_id)

    fire_reason = reason
    if verdict.force_immediate and verdict.immediate_reason:
        fire_reason = verdict.immediate_reason
        consecutive = max(consecutive, 1)
    elif reason is None:
        return None
    elif consecutive < _threshold(reason):
        return None

    assert fire_reason is not None
    if not str(fire_reason).strip():
        logger.warning("tick SOS refused blank reason root=%s", root_id)
        return None
    if not tick_heal_enabled():
        logger.info("tick heal disabled root=%s reason=%s", root_id, fire_reason)
        return None
    if not claim_tick_sos(root_id, fire_reason):
        return None
    reason = fire_reason

    result: dict[str, Any] = {
        "root_id": root_id,
        "reason": reason,
        "consecutive": consecutive,
        "paged": False,
        "bus_noted": False,
        "cdp_execution_id": None,
    }
    try:
        result["paged"] = await notify_tick_sos(
            root_id=root_id,
            reason=reason,
            detail=detail,
            consecutive=consecutive,
        )
    except Exception:  # noqa: BLE001 — SOS must not abort tick
        logger.exception("tick SOS pager failed root=%s", root_id)

    try:
        result["bus_noted"] = await _post_cursor_auto_note(
            root_id,
            reason=reason,
            consecutive=consecutive,
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        logger.exception("tick SOS bus note failed root=%s", root_id)

    if _sos_cdp_enabled():
        try:
            result["cdp_execution_id"] = await _submit_cdp_heal(
                root_id,
                reason=reason,
                consecutive=consecutive,
                detail=detail,
            )
        except Exception:  # noqa: BLE001
            logger.exception("tick SOS CDP submit failed root=%s", root_id)

    logger.warning(
        "charter-tick SOS fired root=%s reason=%s consecutive=%s paged=%s cdp=%s",
        root_id,
        reason,
        consecutive,
        result["paged"],
        result["cdp_execution_id"],
    )
    return result


async def _post_cursor_auto_note(
    root_id: str,
    *,
    reason: str,
    consecutive: int,
    detail: str,
) -> bool:
    from . import bus_client

    body = "\n".join(
        [
            "TYPE: NOTE",
            "tag: tick-sos",
            "",
            f"Automatic tick SOS on root #{root_id}.",
            f"- reason: `{reason}`",
            f"- consecutive: {consecutive}",
            f"- detail: {detail or '(none)'}",
            "",
            "Kaywan can dig here / via cursor-auto. CDP operator-proxy may also",
            "be running a heal mission (purpose=operator-proxy via team_dispatch).",
            "Doctrine: decision:tick-heal-cdp-operator-default",
        ]
    )
    await bus_client.post_root_turn(
        root_id,
        to="cursor",
        subject=f"SOS — tick silent-starve · {reason}",
        body=body,
    )
    return True


__all__ = [
    "classify_skip_for_sos",
    "clear_tick_sos",
    "fire_episode_actuator",
    "maybe_fire_tick_sos",
    "note_observation",
    "observe_kernel_outcome",
    "tick_heal_enabled",
]
