"""Silent-starve exits: per-root skip telemetry + parameterized state-close.

Owns close→unenroll under A4 for ``no_gated_pickup`` and ``stale_window``
(exact ``stopped:stale_window`` only). Extracted from tick_loop so the
ineligible-path control flow stays testable without bloating CharterRunnerTickLoop.
"""

from __future__ import annotations

from typing import Any, Protocol

from universal_logging import get_logger

from scripts.model_manager import observation_event as events

from . import bus_client
from .eligibility import ENROLLMENT_TAG, Decision
from .state_close_compose import prepare_state_close_summary

logger = get_logger(__name__)

# A4 — bound first-tick blast radius (historic arc-complete tagged roots).
MAX_STATE_CLOSES_PER_TICK = 1


class _CapsCheck(Protocol):
    def check(self, root_id: str) -> tuple[bool, str | None]: ...


def checkpoint_turn_number(checkpoint: dict | None) -> int | None:
    """Return checkpoint turn_number when present; None when absent."""
    if not checkpoint:
        return None
    try:
        return int(checkpoint.get("turn_number") or 0) or None
    except (TypeError, ValueError):
        return None


def enrollment_tag_absent(tags_or_result: Any) -> bool:
    """True when ENROLLMENT_TAG is absent from a tags list or thread/result dict.

    Prefer this over ``bool(dict)`` — empty success bodies must not report failure.
    """
    if tags_or_result is None:
        return False
    if isinstance(tags_or_result, dict):
        tags = tags_or_result.get("tags")
        if tags is None and "thread" in tags_or_result:
            tags = (tags_or_result.get("thread") or {}).get("tags")
        if tags is None:
            # Explicit helper field from unenroll_root.
            if "unenrolled" in tags_or_result:
                return bool(tags_or_result["unenrolled"])
            return False
        return ENROLLMENT_TAG not in list(tags or [])
    if isinstance(tags_or_result, (list, tuple, set)):
        return ENROLLMENT_TAG not in list(tags_or_result)
    return False


async def _thread_already_closed(root_id: str) -> bool:
    """Best-effort probe: True when bus thread status is already closed (A5)."""
    try:
        detail = await bus_client.fetch_thread(root_id)
    except Exception:  # noqa: BLE001 — probe failure ⇒ not already-closed
        return False
    status = str(detail.get("status") or "").lower()
    if not status and isinstance(detail.get("thread"), dict):
        status = str((detail.get("thread") or {}).get("status") or "").lower()
    return status == "closed"


async def maybe_state_close_root(
    decision: Decision,
    *,
    reason: str,
    state_closes_this_tick: int,
    max_state_closes: int = MAX_STATE_CLOSES_PER_TICK,
) -> int:
    """Close→unenroll one root under A4; emit ``root_closed`` with ``reason``.

    Close runs first; unenroll only after close success (A3). Already-closed /
    no-op close counts as success so a later tick can retry unenroll (A5).
    Failing closes emit ``closed=False`` without consuming A4 budget — distinct
    from A4 deferral (which emits no ``root_closed``).
    Returns the updated ``state_closes_this_tick`` count.
    """
    if state_closes_this_tick >= max_state_closes:
        return state_closes_this_tick

    ckpt = checkpoint_turn_number(decision.checkpoint)
    closed = False
    unenrolled = False
    close_summary = (
        f"charter-runner state-close reason={reason} checkpoint_turn={ckpt}"
    )
    try:
        rendered, _uri = await prepare_state_close_summary(
            root_id=decision.root_id,
            reason=reason,
            checkpoint_turn=ckpt,
        )
        close_summary = rendered
    except Exception:  # noqa: BLE001 — fall back to machine one-liner in comment only
        logger.exception(
            "charter-runner closeout render failed for root %s — using fallback",
            decision.root_id,
        )
    try:
        await bus_client.close_root_thread(
            decision.root_id,
            summary=close_summary,
        )
        closed = True
    except Exception:  # noqa: BLE001 — mirror harvest swallow/log; A5 probe
        if await _thread_already_closed(decision.root_id):
            closed = True
            logger.info(
                "charter-runner state-close already-closed for root %s "
                "(treating as success for unenroll retry)",
                decision.root_id,
            )
        else:
            logger.exception(
                "charter-runner state-close failed for root %s", decision.root_id
            )

    if closed:
        try:
            result = await bus_client.unenroll_root(decision.root_id)
            unenrolled = enrollment_tag_absent(result)
        except Exception:  # noqa: BLE001 — close succeeded; tag hygiene best-effort
            logger.exception(
                "charter-runner unenroll failed for root %s", decision.root_id
            )
        state_closes_this_tick += 1

    await events.emit_manage_charter_tick_root_closed(
        root=decision.root_id,
        reason=reason,
        checkpoint_turn=ckpt,
        closed=closed,
        unenrolled=unenrolled,
    )
    return state_closes_this_tick


async def emit_skip_and_maybe_state_close(
    decision: Decision,
    *,
    state_closes_this_tick: int,
    skipped_by_reason: dict[str, int],
    max_state_closes: int = MAX_STATE_CLOSES_PER_TICK,
    caps: _CapsCheck | None = None,
) -> int:
    """Emit root_skipped; state-close on no_gated_pickup or exact stale stop.

    Invokes ``maybe_state_close_root`` when:
    - ``decision.reason == \"no_gated_pickup\"``, or
    - ``decision.reason == \"window_in_flight\"`` and
      ``caps.check(root) == (False, \"stopped:stale_window\")`` (A1 exact match).

    Returns the updated ``state_closes_this_tick`` count. Mutates
    ``skipped_by_reason`` in place for the aggregate scanned payload.
    """
    reason = decision.reason
    ckpt = checkpoint_turn_number(decision.checkpoint)
    wip_snippet: str | None = None
    if reason == "wip_active" and decision.parsed is not None:
        wip_snippet = (decision.parsed.wip_text or "")[:120] or None
    await events.emit_manage_charter_tick_root_skipped(
        root=decision.root_id,
        reason=reason,
        checkpoint_turn=ckpt,
        half=decision.half,
        predicate_id=decision.predicate_id,
        wip_snippet=wip_snippet,
        fingerprint=decision.residue_fingerprint,
    )
    skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1

    close_reason: str | None = None
    if reason == "no_gated_pickup":
        close_reason = "no_gated_pickup"
    elif reason == "window_in_flight" and caps is not None:
        # A1: exact equality only — forbid startswith on stopped:*.
        if caps.check(decision.root_id) == (False, "stopped:stale_window"):
            close_reason = "stale_window"

    if close_reason is None:
        return state_closes_this_tick

    return await maybe_state_close_root(
        decision,
        reason=close_reason,
        state_closes_this_tick=state_closes_this_tick,
        max_state_closes=max_state_closes,
    )
