"""Alert-only GIW drain deadline — keep awaiting; force-preempt is the unwedge.

`[universal:obs-over-timeouts]`: ``manage.restart.timeout`` pages; it does not
terminalize the intent or release ``_BLOCKS_NEW_RESTART``. A later matching
``git_worker.drain.completed`` must still SIGTERM the same intent
(``todo:drain-intent-timeout-keep-awaiting`` E-prime).
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

from .restart_intent_states import (
    STATUS_CANCELLED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
)

_SERVICE = "git_integration_worker"
_SUPERVISE_BY_SERVICE: dict[str, asyncio.Task[None]] = {}

WaitSupervise = Callable[[str], Awaitable[bool]]


def timeout_affordances(intent_id: str) -> list[str]:
    """Operator verbs after an alert — cancel unwedge or force-preempt kill."""
    return [
        "inspect: manage(action='busy_status')",
        (
            "cancel: manage(action='cancel_restart_intent', "
            f"intent_id='{intent_id}')"
        ),
        (
            "explicit force: manage(action='restart', "
            "service='git_integration_worker', force=true)"
        ),
    ]


def cas_force_preempt(store: Any, intent_id: str) -> bool:
    """CAS ``pending_drain`` → ``force_requested``. True when this caller won."""
    return (
        store.advance_if_status(
            intent_id,
            from_status=STATUS_PENDING_DRAIN,
            to_status=STATUS_FORCE_REQUESTED,
            reason="force-preempt keep-awaiting drain",
        )
        == 1
    )


def register_supervise_task(service: str, task: asyncio.Task[None]) -> None:
    """Remember the live supervise task so force can wait for gate release."""
    _SUPERVISE_BY_SERVICE[service] = task

    def _drop(done: asyncio.Task[None]) -> None:
        if _SUPERVISE_BY_SERVICE.get(service) is done:
            _SUPERVISE_BY_SERVICE.pop(service, None)

    task.add_done_callback(_drop)


async def wait_supervise_exit(service: str, *, timeout_s: float = 15.0) -> bool:
    """Wait until the service's supervise task has released the restart mutex."""
    task = _SUPERVISE_BY_SERVICE.get(service)
    if task is None or task.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
    except TimeoutError:
        return False
    return True


async def preempt_keep_awaiting_giw(
    store: Any, *, wait_exit: WaitSupervise
) -> dict[str, Any] | None:
    """Abort a keep-awaiting GIW intent without ``release_drain``.

    Returns an error envelope when the awaiter does not exit; ``None`` means
    the caller may ``run_gated(..., force=true)``.
    """
    existing = store.active_for_service(_SERVICE)
    if existing is None or existing.status != STATUS_PENDING_DRAIN:
        return None
    if not cas_force_preempt(store, existing.intent_id):
        return None
    if not await wait_exit(_SERVICE):
        return {
            "status": "error",
            "reason": "force_preempt_awaiter_still_running",
            "intent_id": existing.intent_id,
            "service": _SERVICE,
        }
    return None


async def preempt_giw_keep_await_if_needed(
    ctl: Any, service: str, force: bool
) -> dict[str, Any] | None:
    """Thin manage-side entry: no-op unless GIW ``force=true``."""
    if service != _SERVICE or not force:
        return None
    return await preempt_keep_awaiting_giw(
        ctl.restart_intent_store, wait_exit=wait_supervise_exit
    )


def repair_timeout_intent_gap(store: Any) -> list[dict[str, str]]:
    """Close hidden ``timeout`` rows: restore keep-await or cancel as superseded.

    ``STATUS_TIMEOUT`` is not in ``_BLOCKS_NEW_RESTART``, so ``busy_status``
    drops the intent while a supervisor may still be awaiting. E-prime stops
    writing timeout; this repairs rows that already did.
    """
    repaired: list[dict[str, str]] = []
    listing = getattr(store, "intents_with_status", None)
    if listing is None:
        return repaired
    for intent in listing(STATUS_TIMEOUT):
        live = store.active_for_service(intent.service)
        if live is not None and live.intent_id != intent.intent_id:
            if (
                store.advance_if_status(
                    intent.intent_id,
                    from_status=STATUS_TIMEOUT,
                    to_status=STATUS_CANCELLED,
                    reason=(
                        "repair timeout gap: superseded by " + live.intent_id
                    ),
                )
                == 1
            ):
                repaired.append(
                    {
                        "intent_id": intent.intent_id,
                        "action": "cancelled_superseded",
                        "superseded_by": live.intent_id,
                    }
                )
            continue
        try:
            restored = store.advance_if_status(
                intent.intent_id,
                from_status=STATUS_TIMEOUT,
                to_status=STATUS_PENDING_DRAIN,
                reason="repair timeout gap: restore keep-await",
            )
        except sqlite3.IntegrityError:
            if (
                store.advance_if_status(
                    intent.intent_id,
                    from_status=STATUS_TIMEOUT,
                    to_status=STATUS_CANCELLED,
                    reason="repair timeout gap: pending unique",
                )
                == 1
            ):
                repaired.append(
                    {
                        "intent_id": intent.intent_id,
                        "action": "cancelled_unique",
                    }
                )
            continue
        if restored == 1:
            repaired.append(
                {"intent_id": intent.intent_id, "action": "restored"}
            )
    return repaired


__all__ = [
    "cas_force_preempt",
    "preempt_giw_keep_await_if_needed",
    "preempt_keep_awaiting_giw",
    "register_supervise_task",
    "repair_timeout_intent_gap",
    "timeout_affordances",
    "wait_supervise_exit",
]
