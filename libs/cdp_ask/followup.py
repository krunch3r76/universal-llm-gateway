"""Warm CSE followup — paste drive after attached-lane resolution.

See ``followup_resolve`` for the identity ladder. Default path never registers a
lane or ``goto``s a CSE URL; opt-in ``reattach`` delegates to ``followup_reattach``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.project_ask_conversation import send_followup_paste_half
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_events import (
    cdp_ask_followup_paste_attempt,
    cdp_ask_followup_paste_verified,
    cdp_ask_followup_reattach_attempt,
    cdp_ask_followup_reattach_result,
    cdp_ask_followup_unbound_capped,
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
from cdp_ask.followup_resolve import (
    fail_followup,
    lane_not_attached_detail,
    normalize_cse_url,
    resolve_followup_target,
)
from cdp_ask.models import (
    FollowupMinReceipt,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    FollowupReceipt,
    TargetBinding,
)
from cdp_ask.runner import resolve_followup_prompt

_lane_locks: dict[str, asyncio.Lock] = {}
_inflight_guard = asyncio.Lock()
_REATTACH_ELIGIBLE_ERRORS = frozenset({"cse_not_found_on_lane", "lane_not_attached"})
_RECEIPT_RANK: dict[str, int] = {"dom_paste": 1, "dom_committed": 2}
_DOM_RECEIPTS = frozenset({"dom_paste", "dom_committed"})


def _receipt_rank(receipt: FollowupReceipt | None) -> int:
    if receipt is None:
        return 0
    return _RECEIPT_RANK.get(receipt, 0)


def receipt_meets(
    receipt: FollowupReceipt | None, min_receipt: FollowupMinReceipt
) -> bool:
    """True when proven *receipt* satisfies the caller gate."""
    if min_receipt == "human_visible":
        return False
    return _receipt_rank(receipt) >= _RECEIPT_RANK.get(min_receipt, 0)


def _cap_receipt_for_lane(
    receipt: FollowupReceipt | None, *, lane_created: bool
) -> FollowupReceipt | None:
    """Cap receipt at DOM rungs when a satellite lane was minted (B2)."""
    if receipt is None:
        return None
    if lane_created and receipt not in _DOM_RECEIPTS:
        return "dom_committed" if _receipt_rank(receipt) >= 2 else "dom_paste"
    return receipt


def _cap_receipt_for_unbound(
    receipt: FollowupReceipt | None,
    *,
    target_binding: TargetBinding | None,
) -> FollowupReceipt | None:
    """Further cap unbound pastes to automation-visible DOM rungs only."""
    capped = _cap_receipt_for_lane(receipt, lane_created=False)
    if target_binding != "unbound" or capped is None:
        return capped
    if capped not in _DOM_RECEIPTS:
        return "dom_paste"
    return capped


def _apply_receipt_caps(
    receipt: FollowupReceipt | None,
    *,
    lane_created: bool,
    target_binding: TargetBinding | None,
) -> FollowupReceipt | None:
    capped = _cap_receipt_for_lane(receipt, lane_created=lane_created)
    return _cap_receipt_for_unbound(capped, target_binding=target_binding)


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


def _response_extra(
    *,
    reattach_used: bool,
    lane_created: bool,
) -> dict[str, bool]:
    return {"reattach_used": reattach_used, "lane_created": lane_created}


def _paste_response(
    *,
    req: FollowupProjectAskRequest,
    target_registration_id: str,
    url: str | None,
    pasted_at: float | None,
    streaming: bool | None,
    receipt: FollowupReceipt | None,
    lane_created: bool,
    reattach_used: bool,
    target_binding: TargetBinding | None,
) -> FollowupProjectAskResponse:
    """Build followup response from proven receipt and caller gate."""
    binding: TargetBinding = target_binding or ("unbound" if lane_created else "explicit")
    receipt = _apply_receipt_caps(
        receipt, lane_created=lane_created, target_binding=binding
    )
    if binding == "unbound" and receipt is not None:
        emit_followup_event(
            cdp_ask_followup_unbound_capped(
                registration_id=target_registration_id,
                receipt=receipt,
                target_binding="unbound",
            )
        )
    send_verified = receipt is not None
    ok = receipt_meets(receipt, req.min_receipt)
    extra = _response_extra(reattach_used=reattach_used, lane_created=lane_created)
    if not ok:
        return FollowupProjectAskResponse(
            ok=False,
            url=url,
            registration_id=target_registration_id,
            execution_id=req.execution_id,
            pasted_at=pasted_at,
            send_verified=send_verified,
            receipt=receipt,
            streaming_at_paste=streaming,
            error="send_unverified",
            target_binding=binding,
            **extra,
        )
    return FollowupProjectAskResponse(
        ok=True,
        url=url,
        registration_id=target_registration_id,
        execution_id=req.execution_id,
        pasted_at=pasted_at,
        send_verified=send_verified,
        receipt=receipt,
        streaming_at_paste=streaming,
        target_binding=binding,
        **extra,
    )


async def _maybe_reattach(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
    err: FollowupProjectAskResponse,
) -> tuple[ReattachOutcome | None, FollowupProjectAskResponse | None]:
    """Run opt-in reattach when resolution failed with an eligible typed error."""
    if not req.reattach:
        return None, err
    if not (req.chat_url or "").strip():
        return None, fail_followup("reattach_requires_chat_url")
    if err.error not in _REATTACH_ELIGIBLE_ERRORS:
        return None, err

    holder = await _resolve_holder(req, store)
    emit_followup_event(
        cdp_ask_followup_reattach_attempt(
            chat_url=req.chat_url or "",
            holder=holder,
            purpose=req.purpose,
        )
    )
    outcome = await ensure_cse_attached(
        req.chat_url or "",
        holder=holder,
        purpose=req.purpose,
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
    """Tear down reattach side-effects — deregister created lanes or close opened tabs."""
    if outcome is None or not outcome.ok:
        return
    if outcome.lane_created:
        if retain_lane:
            await _disconnect_playwright(outcome.pw)
        else:
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
                **_response_extra(
                    reattach_used=reattach_used,
                    lane_created=lane_created,
                ),
            )

    assert target is not None
    assert resolution_path is not None
    binding: TargetBinding = target_binding or target.target_binding
    if reattach_used and lane_created and binding != "resolver":
        binding = "explicit"
    extra = _response_extra(reattach_used=reattach_used, lane_created=lane_created)

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
        capped = _apply_receipt_caps(
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

        resp = _paste_response(
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
