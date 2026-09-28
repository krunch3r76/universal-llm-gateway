"""Start a git-worker whose drain target is already gone.

A pending drain intent used to keep-await after the process exited. These
helpers start the worker without a preceding SIGTERM only when the start
callable reports a new process, and they refuse to start after a stop that
did not confirm death.

The process image is the checkout. ``code_ref`` on the intent's validation row
is the verify target, not a commit to check out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from charter_runner_store.propagation_validation import latest_validation_for_intent
from universal_logging import get_logger

from .git_worker_activation_verify import arm_verify_after_generation_gone
from .restart_intent_states import STATUS_COMPLETED, STATUS_FAILED, STATUS_PENDING_DRAIN
from .restart_intent_store import Intent, RestartIntentStore

logger = get_logger(__name__)

# Actions whose drain may start a worker that the liveness predicate calls dead.
# Stop (and anything else) must leave a gone worker down.
START_ON_DEAD_ACTIONS = frozenset({"restart", "sync_restart", "recycle_giw"})
# After a confirmed stop, start must finish inside this window. The in-flight
# start is joined — it is not cancelled and retried — so a child that is still
# coming up is not double-spawned.
START_GAP_S = 15.0

StartCaller = Callable[[], Awaitable[str]]
ReadPid = Callable[[], Awaitable[int | None]]


def drain_supervisor_start(action: str, start: StartCaller) -> StartCaller | None:
    """Pass ``start`` only for restart-class actions. Stop gets None."""
    if action in START_ON_DEAD_ACTIONS:
        return start
    return None


def stop_death_unconfirmed(message: str) -> bool:
    """True when stop did not prove the process is gone."""
    text = message.lower()
    return (
        "could not confirm death" in text
        or "may still be running" in text
        or "cannot stop" in text
        or "death unconfirmed" in text
        or "stop failed" in text
    )


def start_spawned_new_process(message: str) -> bool:
    """True when start's own report is a new process, not a no-op or a crash."""
    text = message.lower()
    if "already running" in text:
        return False
    if "failed" in text:
        return False
    if "error" in text:
        return False
    return True


def lifecycle_result_certifies(message: str, *, action: str) -> bool:
    """True when the kill/start return may advance the intent as done.

    Stop is certified only when death was confirmed. Restart-class actions
    are certified only when death was confirmed and start spawned a process.
    """
    if stop_death_unconfirmed(message):
        return False
    if action in START_ON_DEAD_ACTIONS and not start_spawned_new_process(message):
        return False
    return True


def intent_code_ref(intent: Intent) -> str | None:
    """Return the activation row's code_ref for this intent, if one exists."""
    try:
        row = latest_validation_for_intent(intent.intent_id)
    except Exception:  # noqa: BLE001 — verify still runs without the label
        logger.debug("activation row lookup failed", exc_info=True)
        return None
    if row is None:
        return None
    code_ref = getattr(row, "code_ref", None)
    return str(code_ref) if code_ref else None


def _fail_pending(store: RestartIntentStore, intent: Intent) -> None:
    current = store.get(intent.intent_id)
    if current is not None and current.status == STATUS_PENDING_DRAIN:
        store.advance(intent.intent_id, status=STATUS_FAILED)


async def force_start_and_validate(
    store: RestartIntentStore,
    intent: Intent,
    start: StartCaller,
    *,
    reason: str,
    prior_pid: int | None = None,
    read_pid: ReadPid | None = None,
) -> str:
    """Start the worker and arm activation verify only if a new process exists.

    Does not SIGTERM. ``reason`` is ``dead_target`` or ``deadline_ceiling``.
    "already running", a failed start, or a re-read pid that still matches
    ``prior_pid`` leaves the intent failed and does not arm verify.
    """
    code_ref = intent_code_ref(intent)
    logger.warning(
        "drain force-start: intent_id=%s reason=%s code_ref=%s",
        intent.intent_id,
        reason,
        code_ref,
    )
    try:
        message = await start()
    except Exception:
        _fail_pending(store, intent)
        raise
    if not start_spawned_new_process(message):
        logger.warning(
            "drain force-start did not spawn: intent_id=%s message=%s",
            intent.intent_id,
            message[:200],
        )
        _fail_pending(store, intent)
        return message
    if read_pid is not None:
        try:
            observed = await read_pid()
        except Exception:
            logger.exception("drain force-start pid re-read failed")
            observed = None
        same_process = (
            isinstance(prior_pid, int)
            and isinstance(observed, int)
            and observed == prior_pid
        )
        if not isinstance(observed, int) or same_process:
            logger.warning(
                "drain force-start pid unchanged: intent_id=%s prior=%s observed=%s",
                intent.intent_id,
                prior_pid,
                observed,
            )
            _fail_pending(store, intent)
            return message
    armed = await arm_verify_after_generation_gone(store, intent)
    if not armed:
        current = store.get(intent.intent_id)
        if current is not None and current.status == STATUS_PENDING_DRAIN:
            store.advance(intent.intent_id, status=STATUS_COMPLETED)
    return message


async def paired_stop_then_start(
    stop: StartCaller,
    start: StartCaller,
    *,
    gap_s: float = START_GAP_S,
) -> str:
    """Stop, confirm down, then start. Join a slow start; do not spawn another.

    An unconfirmed stop (including a stop that raises) does not start. A start
    that is still running when ``gap_s`` elapses is awaited, not cancelled,
    because cancellation does not reap the detached child.
    """
    try:
        stop_message = await stop()
    except Exception:
        logger.exception("git-worker stop failed; start withheld")
        return "git-worker stop failed; start withheld (death unconfirmed)"
    if stop_death_unconfirmed(stop_message):
        logger.warning("git-worker stop did not confirm death: %s", stop_message[:200])
        return stop_message
    start_task = asyncio.create_task(start())
    try:
        message = await asyncio.wait_for(asyncio.shield(start_task), timeout=gap_s)
    except TimeoutError:
        logger.warning(
            "git-worker start still in flight after %.1fs; joining, not retrying",
            gap_s,
        )
        message = await start_task
    except Exception:
        logger.exception("git-worker start failed; not retrying")
        if start_task.done():
            return "git-worker start failed"
        return await start_task
    return message
