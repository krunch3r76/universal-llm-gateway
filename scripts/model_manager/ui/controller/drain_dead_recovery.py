"""Start a git-worker whose drain target is already gone.

A pending drain intent used to keep-await after the process exited: the probe
failure path only alerted, and every later recycle coalesced onto that intent
with "drain already in progress". These helpers start the worker without a
preceding SIGTERM and hand the existing activation row to the verifier.

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

# Two reconcile misses, not one blip. Still immediate next to the 180s idle
# gate and the 600s ceiling that previously never started a corpse.
DEAD_CONFIRM_POLLS = 2
# After stop returns, start must finish inside this window or it is retried.
# The timer starts at process-down, so a hang in start cannot leave the gap open.
START_GAP_S = 15.0

StartCaller = Callable[[], Awaitable[str]]


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


async def force_start_and_validate(
    store: RestartIntentStore,
    intent: Intent,
    start: StartCaller,
    *,
    reason: str,
) -> str:
    """Start the worker and arm activation verify. Does not SIGTERM.

    ``reason`` is ``dead_target`` or ``deadline_ceiling``. On start failure the
    intent leaves ``pending_drain`` so a later start is not coalesced away.
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
        current = store.get(intent.intent_id)
        if current is not None and current.status == STATUS_PENDING_DRAIN:
            store.advance(intent.intent_id, status=STATUS_FAILED)
        raise
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
    """Stop, then start. Retry start if it misses the post-stop window.

    Stop runs only inside this function, which is also the function that
    starts. If stop itself fails, start still runs: a half-dead worker is the
    gap this pairing exists to close.
    """
    try:
        await stop()
    except Exception:
        logger.exception("git-worker stop failed; starting to close the gap")
    try:
        return await asyncio.wait_for(start(), timeout=gap_s)
    except Exception:
        logger.exception(
            "git-worker start missed the %.1fs post-stop gap; watchdog start",
            gap_s,
        )
        return await start()
