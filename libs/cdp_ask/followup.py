"""Warm CSE followup — paste drive after attached-lane resolution.

See ``followup_resolve`` for the identity ladder. Default path never registers a
lane or ``goto``s a CSE URL; opt-in ``reattach`` delegates to ``followup_reattach``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.project_ask_conversation import send_followup_paste_half
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_dormant import (
    park_relaunched_host,
    reattach_chat_url,
    reattach_reason,
)
from cdp_ask.followup_events import (
    cdp_ask_followup_paste_attempt,
    cdp_ask_followup_paste_verified,
    cdp_ask_followup_reattach_attempt,
    cdp_ask_followup_reattach_result,
    cdp_ask_fresh_run_inheritance,
)
from cdp_ask.followup_events import (
    emit as emit_followup_event,
)
from cdp_ask.followup_reattach import (
    ReattachOutcome,
    _disconnect_playwright,
    _teardown_attempt,
    ensure_cse_attached,
)
from cdp_ask.followup_receipts import (
    apply_receipt_caps,
    paste_response,
    receipt_meets,
    response_extra,
)
from cdp_ask.followup_resolve import (
    fail_followup,
    lane_not_attached_detail,
    resolve_followup_target,
)
from cdp_ask.models import (
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    TargetBinding,
)
from cdp_ask.runner import resolve_followup_prompt

_lane_locks: dict[str, asyncio.Lock] = {}
_inflight_guard = asyncio.Lock()
_REATTACH_ELIGIBLE_ERRORS = frozenset(
    {"cse_not_found_on_lane", "lane_not_attached", "attended_dormant"}
)
async def _find_page_on_lane(cdp_url: str, chat_url: str) -> tuple[Any, Any] | None:
    """Connect to *cdp_url* and return ``(page, playwright)`` when URL matches."""
    pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
    target_norm = normalize_cse_url(chat_url)
    try:
        for page in ctx.pages:
            if normalize_cse_url(page.url or "") == target_norm:
                return page, pw
        await pw.stop()
        return None
    except Exception:
        await pw.stop()
        raise


async def _acquire_lane(registration_id: str) -> bool:
    async with _inflight_guard:
        lock = _lane_locks.setdefault(registration_id, asyncio.Lock())
        if lock.locked():
            return False
        await lock.acquire()
        return True


def lane_in_flight(registration_id: str) -> bool:
    """True while a followup paste holds this host's in-process lane lock.

    Hygiene consults this before parking a host: killing Chrome mid-paste would
    lose the turn the caller is waiting on.
    """
    lock = _lane_locks.get(registration_id)
    return bool(lock and lock.locked())


def _release_lane(registration_id: str) -> None:
    lock = _lane_locks.get(registration_id)
    if lock and lock.locked():
        lock.release()


async def _resolve_holder(req: FollowupProjectAskRequest, store: ExecutionStore) -> str:
    """Holder for ``register_lane`` during reattach — prefer the originating execution."""
    if req.execution_id:
        rec = await store.get(req.execution_id)
        if rec is not None and rec.holder:
            return rec.holder
    return "cdp-ask-satellite"


async def _maybe_reattach(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
    err: FollowupProjectAskResponse,
) -> tuple[ReattachOutcome | None, FollowupProjectAskResponse | None]:
    """Reattach when the error is eligible and a dormant seat or opt-in allows it."""
    if err.error not in _REATTACH_ELIGIBLE_ERRORS:
        return None, err
    chat_url = reattach_chat_url(req, err)
    if req.reattach and not chat_url:
        return None, fail_followup("reattach_requires_chat_url")
    reason = reattach_reason(req, chat_url)
    if reason is None or not chat_url:
        return None, err

    holder = await _resolve_holder(req, store)
    emit_followup_event(
        cdp_ask_followup_reattach_attempt(
            chat_url=chat_url,
            holder=holder,
            purpose=req.purpose,
        )
    )
    outcome = await ensure_cse_attached(
        chat_url,
        holder=holder,
        purpose=req.purpose,
        allow_mint=bool(req.reattach),
    )
    emit_followup_event(
        cdp_ask_followup_reattach_result(
            registration_id=outcome.registration_id,
            lane_created=outcome.lane_created,
            ok=outcome.ok,
            error_code=outcome.error,
        )
    )
    if not outcome.ok:
        return outcome, fail_followup(outcome.error or "reattach_navigate_failed")
    return outcome, None


async def _reattach_teardown(
    outcome: ReattachOutcome | None,
    *,
    retain_lane: bool,
) -> None:
    """Tear down reattach side-effects — park woken seats, drop minted lanes.

    ``retain_lane`` is the wait-report contract: disconnect Playwright only.
    Never park, close the operator tab, or deregister the host.
    """
    if outcome is None or not outcome.ok:
        return
    if retain_lane:
        await _disconnect_playwright(outcome.pw)
        return
    if outcome.relaunched:
        await park_relaunched_host(outcome)
        return
    if outcome.lane_created:
        await _teardown_attempt(outcome.page, outcome.pw, close_page=True)
        with contextlib.suppress(Exception):
            cdp_registry.deregister_lane(outcome.registration_id or "")
        return
    await _teardown_attempt(outcome.page, outcome.pw)


async def execute_followup(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
) -> FollowupProjectAskResponse:
    """Resolve identity, paste prompt into live CSE, return paste proof."""
    try:
        prompt = resolve_followup_prompt(req)
    except ValueError:
        return fail_followup("no_prompt")

    if req.reattach and not (req.chat_url or "").strip():
        return fail_followup("reattach_requires_chat_url")

    if req.min_receipt == "human_visible":
        return fail_followup("human_visible_receipt_unavailable")

    reattach_outcome: ReattachOutcome | None = None
    reattach_used = False
    lane_created = False
    declared_target = bool(
        (req.chat_url or "").strip() or (req.registration_id or "").strip()
    )

    target, err, resolution_path, target_binding = await resolve_followup_target(
        req, store
    )
    if err is not None:
        reattach_outcome, reattach_err = await _maybe_reattach(req, store, err)
        if reattach_err is not None:
            return reattach_err
        reattach_used = True
        lane_created = bool(reattach_outcome and reattach_outcome.lane_created)
        target, err, resolution_path, target_binding = await resolve_followup_target(
            req, store
        )
        if err is not None:
            await _reattach_teardown(reattach_outcome, retain_lane=req.retain_lane)
            return fail_followup(
                err.error or "followup_failed",
                detail=err.detail,
                candidates=err.candidates,
                **response_extra(
                    reattach_used=reattach_used,
                    lane_created=lane_created,
                ),
            )

    assert target is not None
    assert resolution_path is not None
    binding: TargetBinding = target_binding or target.target_binding
    if reattach_used and lane_created and binding != "resolver":
        binding = "explicit"
    extra = response_extra(reattach_used=reattach_used, lane_created=lane_created)

    if not await _acquire_lane(target.registration_id):
        await _reattach_teardown(reattach_outcome, retain_lane=req.retain_lane)
        return fail_followup(
            "lane_busy",
            detail="concurrent followup to same registration_id",
            **extra,
        )

    emit_followup_event(
        cdp_ask_followup_paste_attempt(
            registration_id=target.registration_id,
            resolution_path=resolution_path,
        )
    )

    pw = None
    try:
        found = await _find_page_on_lane(target.cdp_url, target.chat_url)
        if found is None:
            resp = fail_followup(
                "cse_not_found_on_lane", detail="page gone before paste", **extra
            )
            emit_followup_event(
                cdp_ask_followup_paste_verified(
                    registration_id=target.registration_id,
                    resolution_path=resolution_path,
                    send_verified=False,
                    streaming_at_paste=None,
                    error_code=resp.error,
                    lane_created=lane_created,
                    receipt=None,
                    target_binding=binding,
                )
            )
            return resp

        page, pw = found
        if normalize_cse_url(page.url or "") != normalize_cse_url(target.chat_url):
            resp = fail_followup(
                "lane_not_attached", detail=lane_not_attached_detail(), **extra
            )
            emit_followup_event(
                cdp_ask_followup_paste_verified(
                    registration_id=target.registration_id,
                    resolution_path=resolution_path,
                    send_verified=False,
                    streaming_at_paste=None,
                    error_code=resp.error,
                    lane_created=lane_created,
                    receipt=None,
                    target_binding=binding,
                )
            )
            return resp

        paste = await send_followup_paste_half(page, prompt)
        receipt = paste.get("receipt")
        streaming = paste.get("streaming_at_paste")
        url = paste.get("url") or target.chat_url
        pasted_at = paste.get("pasted_at")
        capped = apply_receipt_caps(
            receipt, lane_created=lane_created, target_binding=binding
        )
        send_verified = capped is not None

        emit_followup_event(
            cdp_ask_followup_paste_verified(
                registration_id=target.registration_id,
                resolution_path=resolution_path,
                send_verified=send_verified,
                streaming_at_paste=streaming,
                error_code=None if send_verified else "send_unverified",
                lane_created=lane_created,
                receipt=capped,
                target_binding=binding,
            )
        )
        if send_verified:
            emit_followup_event(
                cdp_ask_fresh_run_inheritance(
                    registration_id=target.registration_id,
                    resolution_path=resolution_path,
                    target_binding=binding,
                    reattach_used=reattach_used,
                    declared=declared_target,
                    purpose=req.purpose,
                )
            )

        resp = paste_response(
            req=req,
            target_registration_id=target.registration_id,
            url=url,
            pasted_at=pasted_at,
            streaming=streaming,
            receipt=receipt,
            lane_created=lane_created,
            reattach_used=reattach_used,
            target_binding=binding,
        )

        if (
            resp.ok
            and receipt_meets(capped, "dom_committed")
            and not lane_created
            and not req.retain_lane
        ):
            from claude_bundles.cse_session_obligations import (
                emit_wake_delivered_transition,
                resolve_wake_obligation_for_receipt,
            )

            thread, obligation_id = resolve_wake_obligation_for_receipt(
                target.registration_id
            )
            emit_wake_delivered_transition(
                registration_id=target.registration_id,
                thread=thread,
                obligation_id=obligation_id,
                send_verified=True,
            )

        return resp
    finally:
        if pw is not None:
            await pw.stop()
        await _reattach_teardown(reattach_outcome, retain_lane=req.retain_lane)
        _release_lane(target.registration_id)
