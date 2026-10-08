"""Probe-wait then lifecycle for restart-intent supervisors.

A probe exception is busy (fail closed). The ceiling still runs, so a down
or wedged Stargate self-preempts instead of dying on the first timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from universal_logging import get_logger

from .busy_work_summary import format_active_work_summary
from .restart_intent_lookup import note_waiting_reason
from .restart_intent_reason_codes import (
    BLOCKED_BY_JOB,
    DRAIN_PROBE_EXCEPTION,
    IDLE_CEILING_REACHED,
    IDLE_OBSERVED,
    LIFECYCLE_COMPLETED,
    LIFECYCLE_EXCEPTION,
    SUPERVISOR_EXCEPTION,
    reason_code,
)
from .restart_intent_states import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
)

logger = get_logger(__name__)

_REASON_LIMIT = 240


def describe_probe_exc(exc: BaseException) -> str:
    message = str(exc).strip()
    text = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    return text[:180]


def _clip(text: str) -> str:
    return text if len(text) <= _REASON_LIMIT else text[: _REASON_LIMIT - 3] + "..."


async def supervise_until_lifecycle(
    *,
    store: Any,
    intent: Any,
    probe: Callable[[], Awaitable[Any]],
    is_idle: Callable[[Any], bool],
    lifecycle: Callable[[], Awaitable[str]],
    deadline_s: float,
    poll_interval_s: float,
    ceiling: str,
) -> None:
    """``ceiling`` is ``self_preempt`` or ``extend`` (alert-only, GIW/local)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_s
    intent_id = intent.intent_id
    phase = "wait"
    last_note: str | None = None
    last_cause = "busy"
    probe_key: str | None = None
    probe_count = 0
    escalated = False
    try:
        while True:
            try:
                work = await probe()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — fail closed, keep polling
                key = describe_probe_exc(exc)
                probe_count = probe_count + 1 if key == probe_key else 1
                probe_key = key
                last_cause = reason_code(
                    DRAIN_PROBE_EXCEPTION, f"{type(exc).__name__} x{probe_count}"
                )
                _note_if_changed(store, intent_id, last_cause, last_note)
                last_note = last_cause
            else:
                probe_key = None
                probe_count = 0
                if is_idle(work):
                    break
                summary = format_active_work_summary(getattr(work, "detail", None))
                last_cause = reason_code(BLOCKED_BY_JOB, summary or "busy")
                if last_cause != last_note:
                    _note_if_changed(store, intent_id, last_cause, last_note)
                    last_note = last_cause
            if loop.time() >= deadline:
                if ceiling == "self_preempt":
                    escalated = True
                    phase = "force_requested"
                    if not _cas(
                        store,
                        intent_id,
                        from_status=STATUS_PENDING_DRAIN,
                        to_status=STATUS_FORCE_REQUESTED,
                        reason=_clip(reason_code(IDLE_CEILING_REACHED, last_cause)),
                    ):
                        return
                    break
                deadline = loop.time() + deadline_s
            await asyncio.sleep(poll_interval_s)
        phase = "drained_restarting"
        if escalated:
            drained_reason = reason_code(IDLE_CEILING_REACHED, last_cause)
        elif last_cause.startswith(DRAIN_PROBE_EXCEPTION):
            drained_reason = _clip(reason_code(IDLE_OBSERVED, last_cause))
        else:
            drained_reason = IDLE_OBSERVED
        from_status = STATUS_FORCE_REQUESTED if escalated else STATUS_PENDING_DRAIN
        if not _cas(
            store,
            intent_id,
            from_status=from_status,
            to_status=STATUS_DRAINED_RESTARTING,
            reason=drained_reason,
        ):
            return
        phase = "lifecycle"
        message = await lifecycle()
        phase = "completed"
        _cas(
            store,
            intent_id,
            from_status=STATUS_DRAINED_RESTARTING,
            to_status=STATUS_COMPLETED,
            reason=_clip(reason_code(LIFECYCLE_COMPLETED, message or "")),
        )
        if escalated:
            logger.info(
                "stargate drain supervisor completed after idle-ceiling force "
                "(intent_id=%s)",
                intent_id,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _record_failed(store, intent_id, phase=phase, exc=exc)
        logger.exception(
            "restart drain supervisor failed intent_id=%s phase=%s",
            intent_id,
            phase,
        )
        raise


def _note_if_changed(
    store: Any, intent_id: str, cause: str, previous: str | None
) -> None:
    if cause == previous:
        return
    note_waiting_reason(store, intent_id, status_reason=_clip(cause))


def _cas(
    store: Any,
    intent_id: str,
    *,
    from_status: str,
    to_status: str,
    reason: str,
) -> bool:
    return (
        store.advance_if_status(
            intent_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
        )
        == 1
    )


def _record_failed(
    store: Any, intent_id: str, *, phase: str, exc: BaseException
) -> None:
    exc_name = type(exc).__name__
    if phase == "lifecycle":
        reason = _clip(reason_code(LIFECYCLE_EXCEPTION, exc_name))
    else:
        reason = _clip(reason_code(SUPERVISOR_EXCEPTION, f"{phase}:{exc_name}"))
    try:
        current = store.get(intent_id)
        if current is None or current.status in {STATUS_COMPLETED, STATUS_CANCELLED}:
            return
        store.advance_if_status(
            intent_id,
            from_status=current.status,
            to_status=STATUS_FAILED,
            reason=reason,
        )
    except Exception:
        logger.exception("restart intent failure record failed intent_id=%s", intent_id)
