"""Deliver neutral followups into a parked houseless seat's own reopened tab.

A parked houseless seat row (``houseless_seat_row``) is the address of a live
chat whose tab is closed. This module reopens the row's own tab on a host
(``resolve_resume_host``, ``reopen_tab_row`` with trigger ``wake``), waits up
to ``CDP_HOUSELESS_SEAT_IDENTITY_WAIT_S`` seconds (default 30) for the page to
show the seat's chat, pastes with ``send_followup_paste_half``, then closes the
tab and parks the row again.

Codes: ``houseless_seat_not_parked``, ``houseless_seat_chat_mismatch`` and
``houseless_seat_in_flight`` are refusals with nothing opened; ``lane_busy``
is the existing concurrency refusal; ``houseless_seat_unavailable`` covers
recoverable failures (cause in ``detail``).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Callable
from typing import Any

from claude_bundles import cdp_registry_store as _store
from claude_bundles.cdp_registry.execution_state import (
    in_flight_entry_contributes_restart_gate_busy,
    row_execution_in_flight,
)
from claude_bundles.cdp_registry.houseless_seat_row import (
    HOUSELESS_SEAT,
    is_parked_houseless_seat,
)
from claude_bundles.cdp_registry.models import STATUS_TAB
from claude_bundles.cdp_registry.tab_rows_reopen import _keep_tab_row, reopen_tab_row
from claude_bundles.cse_url import claude_seat_identity_key
from claude_bundles.project_ask_conversation import send_followup_paste_half
from universal_logging import get_logger

from cdp_ask.followup_envelope import fail_followup
from cdp_ask.followup_receipts import apply_receipt_caps, paste_response, receipt_meets
from cdp_ask.models import FollowupProjectAskRequest, FollowupProjectAskResponse

logger = get_logger(__name__)

NOT_PARKED = "houseless_seat_not_parked"
CHAT_MISMATCH = "houseless_seat_chat_mismatch"
IN_FLIGHT = "houseless_seat_in_flight"
UNAVAILABLE = "houseless_seat_unavailable"
IDENTITY_WAIT_ENV = "CDP_HOUSELESS_SEAT_IDENTITY_WAIT_S"
DEFAULT_IDENTITY_WAIT_S = 30.0
_IDENTITY_POLL_S = 0.25


def identity_wait_s() -> float:
    raw = os.environ.get(IDENTITY_WAIT_ENV, "").strip()
    if not raw:
        return DEFAULT_IDENTITY_WAIT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_IDENTITY_WAIT_S
    return value if value > 0 else DEFAULT_IDENTITY_WAIT_S


def is_houseless_seat_tab_row(row: Any) -> bool:
    from claude_bundles.what_is_running_view import is_houseless_purpose

    return (
        isinstance(row, dict)
        and row.get("status") == STATUS_TAB
        and is_houseless_purpose(row.get("purpose"))
    )


def _refusal(row: Any, req: FollowupProjectAskRequest) -> str | None:
    if not is_parked_houseless_seat(row):
        return NOT_PARKED
    want = claude_seat_identity_key(req.chat_url or "")
    if not want or want != claude_seat_identity_key(str(row.get("chat_url") or "")):
        return CHAT_MISMATCH
    entry = row_execution_in_flight(row)
    if entry is not None and in_flight_entry_contributes_restart_gate_busy(entry):
        return IN_FLIGHT
    return None


def _unavailable(
    req: FollowupProjectAskRequest, cause: str
) -> FollowupProjectAskResponse:
    logger.warning(
        "houseless seat tab unavailable registration_id=%s cause=%s",
        req.registration_id,
        cause,
    )
    return fail_followup(UNAVAILABLE, detail=cause, url=req.chat_url)


async def _wait_for_identity(
    page: Any, chat_url: str, match: Callable[[str, str], bool]
) -> str | None:
    deadline = time.monotonic() + identity_wait_s()
    while True:
        url = str(getattr(page, "url", "") or "")
        if match(chat_url, url):
            return None
        if time.monotonic() >= deadline:
            return url
        await asyncio.sleep(_IDENTITY_POLL_S)


def _paste_result(
    req: FollowupProjectAskRequest, rid: str, chat_url: str, paste: dict[str, Any]
) -> FollowupProjectAskResponse:
    receipt = paste.get("receipt")
    resp = paste_response(
        req=req,
        target_registration_id=rid,
        url=paste.get("url") or chat_url,
        pasted_at=paste.get("pasted_at"),
        streaming=paste.get("streaming_at_paste"),
        receipt=receipt,
        lane_created=False,
        reattach_used=False,
        target_binding="explicit",
    )
    capped = apply_receipt_caps(receipt, lane_created=False, target_binding="explicit")
    if resp.ok and receipt_meets(capped, "dom_committed") and not req.retain_lane:
        from claude_bundles.cse_session_obligations import (
            emit_wake_delivered_transition,
            resolve_wake_obligation_for_receipt,
        )

        thread, obligation_id = resolve_wake_obligation_for_receipt(rid)
        emit_wake_delivered_transition(
            registration_id=rid,
            thread=thread,
            obligation_id=obligation_id,
            send_verified=True,
        )
    return resp


async def _reopen_paste_repark(
    req: FollowupProjectAskRequest, row: dict[str, Any], rid: str, prompt: str
) -> FollowupProjectAskResponse:
    from cdp_ask.execution_resume import _seat_identities_match
    from cdp_ask.execution_resume_tab import _select_target_page, resolve_resume_host

    chat_url = str(row.get("chat_url") or "")
    reopened = False
    pw: Any = None
    selected = None
    try:
        try:
            host = await asyncio.to_thread(resolve_resume_host, row, trigger="wake")
            reopened = True
            await asyncio.to_thread(
                reopen_tab_row, rid, str(host["registration_id"]), "wake"
            )
            fresh = (await asyncio.to_thread(_store.load_active)).get(rid) or {}
            target_id = str(fresh.get("target_id") or "").strip()
            port = fresh.get("port")
            if not target_id or not isinstance(port, int):
                return _unavailable(req, "reopen recorded no target")
            selected = await _select_target_page(f"http://127.0.0.1:{port}", target_id)
        except Exception as exc:  # noqa: BLE001 — every reopen failure is recoverable
            return _unavailable(req, f"{type(exc).__name__}: {exc}")
        if selected is None:
            return _unavailable(req, "target page not found")
        page, pw = selected
        last_url = await _wait_for_identity(page, chat_url, _seat_identities_match)
        if last_url is not None:
            return _unavailable(req, f"identity unconfirmed; last url {last_url!r}")
        paste = await send_followup_paste_half(page, prompt)
        return _paste_result(req, rid, chat_url, paste)
    finally:
        if pw is not None:
            with contextlib.suppress(Exception):
                await pw.stop()
        if reopened:
            await asyncio.to_thread(
                _keep_tab_row, rid, reason=HOUSELESS_SEAT, close_target=True
            )


async def deliver_houseless_seat_tab(
    req: FollowupProjectAskRequest, row: dict[str, Any], prompt: str
) -> FollowupProjectAskResponse:
    from cdp_ask.followup import _acquire_lane, _release_lane

    rid = str(row.get("registration_id") or req.registration_id or "").strip()
    refusal = _refusal(row, req)
    if refusal is not None:
        return fail_followup(refusal, url=req.chat_url)
    if not await _acquire_lane(rid, target_chat_url=str(row.get("chat_url") or "")):
        return fail_followup(
            "lane_busy", detail="concurrent followup to same registration_id"
        )
    try:
        current = (await asyncio.to_thread(_store.load_active)).get(rid)
        refusal = _refusal(current, req)
        if refusal is not None:
            return fail_followup(refusal, url=req.chat_url)
        return await _reopen_paste_repark(req, current, rid, prompt)
    finally:
        _release_lane(rid)
